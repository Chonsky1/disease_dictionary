"""LangGraph RAG 파이프라인: retrieve → generate 두 노드 (F2003, F2004, F2006~F2008).

retrieve 안의 단계
  1. 선택한 감염병의 메타데이터 청크를 ID로 조회 (검색 없이 항상 [근거 1])
  2. 프롬프트 1로 검색어 추출
  3. 항목 자체 문서를 확인·색인 (어떤 제외 규칙보다 우선)
  4. 검색어마다 수집: 캐시 확인 → 위키 후보 검색 → 프롬프트 2 선별 → 본문 수집 → 청킹·색인
  5. 질문과의 의미 유사도로 외부 문서 청크 검색 (disease_id 필터, Top-k·검색 방식은 설정)
  6. 병합·중복 제거 후 근거 번호 부여
generate
  7. 프롬프트 3으로 답변 → 본문의 [근거 n]을 찾아 실제 인용된 근거만 남김
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Callable, TypedDict

from langchain_core.language_models import BaseChatModel
from langchain_core.output_parsers import StrOutputParser
from langgraph.graph import END, START, StateGraph

from data_prep.collector import WikiCollector
from data_prep.diseases import Disease
from vectorstore.store import DiseaseStore, text_hash

from .parsing import cited_numbers, parse_json
from .prompts import ANSWER_GENERATION, CANDIDATE_SELECTION, QUERY_EXTRACTION

log = logging.getLogger("kdca_rag")

NOT_FOUND_TEXT = "제공된 문서에서 확인되지 않습니다."


class IndexMissingError(RuntimeError):
    pass


class RagState(TypedDict, total=False):
    question: str
    disease_id: str
    queries: list[str]
    collect_log: list[dict]
    warnings: list[str]
    evidence: list[dict]
    answer: str
    cited: list[dict]


@dataclass
class RagServices:
    llm: BaseChatModel
    store: DiseaseStore
    collector: WikiCollector | None       # None이면 수집 없이 이미 색인된 문서만 사용
    diseases: dict[str, Disease]
    wiki_titles: dict[str, dict]
    settings: dict


def _emit(event: dict) -> None:
    """진행 상태를 stream_mode='custom'으로 흘려 보낸다 (그래프 밖에서 호출되면 무시)."""
    try:
        from langgraph.config import get_stream_writer
        get_stream_writer()(event)
    except Exception:  # noqa: BLE001 - 진행 표시는 실패해도 본 기능에 영향 없음
        pass


def _invoke_text(llm: BaseChatModel, prompt, **kwargs) -> str:
    return (prompt | llm | StrOutputParser()).invoke(kwargs)


# --- 프롬프트 1 ---------------------------------------------------------------------
def extract_queries(llm: BaseChatModel, item_text: str, question: str, max_queries: int) -> list[str]:
    raw = _invoke_text(llm, QUERY_EXTRACTION, item_text=item_text, question=question, max_queries=max_queries)
    try:
        data = parse_json(raw)
        queries = data.get("queries", []) if isinstance(data, dict) else data
    except ValueError:
        log.warning("검색어 추출 결과를 파싱하지 못해 검색어 없이 진행")
        queries = []
    clean = [" ".join(str(q).split()) for q in queries if str(q).strip()]
    return list(dict.fromkeys(clean))[:max_queries]


# --- 프롬프트 2 ---------------------------------------------------------------------
def make_selector(llm: BaseChatModel, disease_name: str, question: str, query: str,
                  max_selected: int) -> Callable[[list[dict]], list[int]]:
    def select(cands: list[dict]) -> list[int]:
        labeled = {f"c{i + 1}": c for i, c in enumerate(cands)}
        listing = "\n".join(f"{k}. {c['title']} — {c['summary'][:200]}" for k, c in labeled.items())
        raw = _invoke_text(llm, CANDIDATE_SELECTION, disease_name=disease_name, question=question,
                           query=query, candidates=listing, max_selected=max_selected)
        try:
            data = parse_json(raw)
            picked = data.get("selected", []) if isinstance(data, dict) else data
        except ValueError:
            log.warning("후보 선별 결과를 파싱하지 못해 이 검색어는 건너뜀")
            picked = []
        return [labeled[str(p).strip()]["pageid"] for p in picked if str(p).strip() in labeled]
    return select


# --- 근거 구성 --------------------------------------------------------------------
def build_evidence(meta_doc, doc_hits) -> list[dict]:
    """메타데이터 청크를 맨 앞에 고정하고, 외부 문서 청크는 청크 ID·본문 해시로 중복 제거한다."""
    items, seen_ids, seen_hash = [], set(), set()
    m = meta_doc.metadata
    items.append({"chunk_id": meta_doc.id, "source_type": "meta", "source_label": "항목 메타데이터",
                  "title": m["title"], "section": "", "url": m["source_url"],
                  "wiki_url": m.get("wiki_url", ""), "text": meta_doc.page_content})
    seen_ids.add(meta_doc.id)
    for d in doc_hits:
        h = text_hash(d.page_content)
        if d.id in seen_ids or h in seen_hash:
            continue
        seen_ids.add(d.id)
        seen_hash.add(h)
        md = d.metadata
        items.append({"chunk_id": d.id, "source_type": md.get("source_type", "wiki"),
                      "source_label": md.get("source_label", "위키백과"), "title": md["title"],
                      "section": md.get("section", ""), "url": md["source_url"], "text": d.page_content})
    for i, it in enumerate(items, start=1):
        it["n"] = i
    return items


def format_evidence(evidence: list[dict]) -> str:
    blocks = []
    for e in evidence:
        where = f" / {e['section']}" if e.get("section") else ""
        blocks.append(f"[근거 {e['n']}] (출처 유형: {e['source_label']}) 제목: {e['title']}{where}\n{e['text']}")
    return "\n\n".join(blocks)


# --- 노드 ---------------------------------------------------------------------------
def make_retrieve(svc: RagServices):
    def retrieve(state: RagState) -> RagState:
        did, question = state["disease_id"], state["question"]
        disease = svc.diseases[did]
        meta_doc = svc.store.get_meta(did)
        if meta_doc is None:
            raise IndexMissingError("메타데이터 색인이 없습니다.")

        _emit({"stage": "queries", "message": "검색어를 추출하는 중"})
        queries = extract_queries(svc.llm, meta_doc.page_content, question, svc.settings["max_queries"])
        _emit({"stage": "queries", "message": f"검색어: {', '.join(queries) or '(없음)'}"})

        collect_log, warnings = [], []
        if svc.collector is not None:
            try:
                collect_log = _collect(svc, disease, question, queries)
            except Exception as exc:  # noqa: BLE001 - 수집 실패 시 이미 색인된 문서로 계속
                log.exception("외부 문서 수집 실패")
                warnings.append("외부 문서를 수집하지 못해 이미 색인된 문서로 답변합니다.")
                _emit({"stage": "collect", "message": warnings[-1], "level": "warning"})
                _ = exc

        _emit({"stage": "search", "message": "근거를 검색하는 중"})
        hits = svc.store.search_docs(question, did)
        evidence = build_evidence(meta_doc, hits)
        return {"queries": queries, "collect_log": collect_log, "warnings": warnings, "evidence": evidence}
    return retrieve


def _collect(svc: RagServices, disease: Disease, question: str, queries: list[str]) -> list[dict]:
    did, name = disease.disease_id, disease.display_name
    log_rows: list[dict] = []
    own = svc.wiki_titles.get(did, {})
    own_pid = int(own.get("pageid") or 0)

    if own_pid:  # 항목 자체 문서: 선별·길이 등 어떤 제외 규칙보다 우선해 항상 색인
        doc_id = f"wiki-{own_pid}"
        cached = svc.collector.has_page(own_pid)
        if not svc.store.is_indexed(did, doc_id):
            _emit({"stage": "collect", "message": f"항목 문서 수집: {own.get('title')}"})
            page = svc.collector.get_page(own_pid, own["title"])
            n = svc.store.index_page(did, name, page, query=name)
            log_rows.append({"query": name, "kind": "item", "from_cache": cached, "indexed": [page["title"]],
                             "chunks": n, "rejected": []})
        else:
            log_rows.append({"query": name, "kind": "item", "from_cache": True, "indexed": [], "chunks": 0,
                             "rejected": []})

    for q in queries:
        if svc.collector.is_cached(q):
            _emit({"stage": "collect", "message": f"이미 수집한 검색어: {q}"})
        else:
            _emit({"stage": "collect", "message": f"위키백과 검색: {q}"})
        selector = make_selector(svc.llm, name, question, q, svc.settings["max_selected_per_query"])
        res = svc.collector.collect(q, selector, exclude_pageids={own_pid} if own_pid else None)
        indexed, chunks = [], 0
        for page in res.pages:
            if not svc.store.is_indexed(did, page["doc_id"]):
                chunks += svc.store.index_page(did, name, page, query=q)
                indexed.append(page["title"])
        log_rows.append({"query": q, "kind": "query", "from_cache": res.from_cache, "indexed": indexed,
                         "chunks": chunks, "rejected": res.rejected})
    new_titles = [t for r in log_rows for t in r["indexed"]]
    _emit({"stage": "collect", "message": f"새로 색인한 문서 {len(new_titles)}건" +
           (f": {', '.join(new_titles)}" if new_titles else "")})
    return log_rows


def make_generate(svc: RagServices):
    def generate(state: RagState) -> RagState:
        _emit({"stage": "generate", "message": "답변을 생성하는 중"})
        evidence = state["evidence"]
        disease = svc.diseases[state["disease_id"]]
        answer = _invoke_text(svc.llm, ANSWER_GENERATION, disease_name=disease.display_name,
                              question=state["question"], evidence=format_evidence(evidence)).strip()
        nums = cited_numbers(answer, len(evidence))
        cited = [evidence[n - 1] for n in nums]
        return {"answer": answer or NOT_FOUND_TEXT, "cited": cited}
    return generate


def build_graph(svc: RagServices):
    g = StateGraph(RagState)
    g.add_node("retrieve", make_retrieve(svc))
    g.add_node("generate", make_generate(svc))
    g.add_edge(START, "retrieve")
    g.add_edge("retrieve", "generate")
    g.add_edge("generate", END)
    return g.compile()


def run(graph, question: str, disease_id: str, on_event: Callable[[dict], None] | None = None) -> RagState:
    """진행 이벤트를 콜백으로 넘기면서 최종 상태를 돌려준다."""
    final: RagState = {}
    for mode, chunk in graph.stream({"question": question, "disease_id": disease_id},
                                    stream_mode=["custom", "values"]):
        if mode == "custom" and on_event:
            on_event(chunk)
        elif mode == "values":
            final = chunk
    return final
