"""LangGraph RAG 파이프라인: retrieve → generate 두 노드 (F2003, F2004, F2006~F2008).

retrieve 안의 단계
  1. 선택한 감염병의 메타데이터 청크를 ID로 조회 (검색 없이 항상 [근거 1])
  2. 프롬프트 1로 검색어 추출
  3. 항목 자체 문서를 확인·색인 (어떤 제외 규칙보다 우선)
  4. 검색어마다 수집: 캐시 확인 → 위키 후보 검색 → 프롬프트 2 선별 → 본문 수집 → 청킹·색인
  5. 질문과의 의미 유사도로 외부 문서 청크 검색 (disease_id 필터, Top-k·검색 방식은 설정)
  6. 병합·중복 제거 후 근거 번호 부여
generate
  7. 프롬프트 3으로 답변(스트리밍) → 본문의 [근거 n]을 찾아 실제 인용된 근거만 남김

속도 설정 (config/rag_settings.json, 없으면 괄호 안 기본값)
  collect_policy   (background) always: 매번 검색어 추출·수집 / when_needed: 이미 모은 문서가 있으면 건너뜀 /
                   background: 이미 모은 문서로 바로 답하고 수집은 뒤에서 (다음 질문부터 반영).
                   처음 묻는 감염병이면 LLM이 필요 없는 항목 자체 문서만 바로 색인하고 답한다
  min_docs_for_fast (1)  이 감염병에 색인된 문서가 이만큼 있으면 '이미 모은 문서가 있다'고 본다
  parallel_collect (true) 항목 문서·검색어별 수집을 동시에
  aux_model        ("")  검색어 추출·후보 선별에 쓸 가벼운 모델 (비우면 chat_model)
  stream_answer    (true) 답변을 글자 단위로 흘려 보냄 (첫 글자 대기 시간 단축)
  answer_cache     (true) 같은 질문은 저장된 답으로 즉시 응답 (build_runtime이 연결)
시간·호출 수는 rag/metrics.py의 Meter가 기록해 state["timings"]로 돌려준다.
"""
from __future__ import annotations

import contextvars
import logging
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any, Callable, TypedDict

from langchain_core.language_models import BaseChatModel
from langchain_core.output_parsers import StrOutputParser
from langgraph.graph import END, START, StateGraph

from data_prep.collector import WikiCollector
from data_prep.diseases import Disease
from vectorstore.store import DiseaseStore, text_hash

from .answer_cache import AnswerCache
from .metrics import Meter
from .parsing import cited_numbers, parse_json
from .prompts import ANSWER_GENERATION, ANSWER_PROMPT_VERSION, CANDIDATE_SELECTION, QUERY_EXTRACTION

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
    meter: Any              # rag.metrics.Meter (run()이 넣어 줌)
    timings: dict
    background_report: dict  # 지난 질문 뒤 배경 수집 결과 (검색어·새로 색인한 문서·버린 후보), F2003·F2004·F2012 표시용


@dataclass
class RagServices:
    llm: BaseChatModel
    store: DiseaseStore
    collector: WikiCollector | None       # None이면 수집 없이 이미 색인된 문서만 사용
    diseases: dict[str, Disease]
    wiki_titles: dict[str, dict]
    settings: dict
    aux_llm: BaseChatModel | None = None          # 검색어 추출·후보 선별용 (없으면 llm)
    answer_cache: AnswerCache | None = None
    index_lock: threading.Lock = field(default_factory=threading.Lock)   # 색인 쓰기 (배경 수집과 겹치지 않게)
    background: set = field(default_factory=set)  # 배경 수집 중인 감염병 ID
    background_log: dict = field(default_factory=dict)   # 감염병 ID → 마지막 배경 수집 결과 (다음 답변에서 보여 줌)

    @property
    def helper_llm(self) -> BaseChatModel:
        return self.aux_llm or self.llm

    def opt(self, key: str, default):
        return self.settings.get(key, default)


def _emit(event: dict) -> None:
    """진행 상태를 stream_mode='custom'으로 흘려 보낸다 (그래프 밖에서 호출되면 무시)."""
    try:
        from langgraph.config import get_stream_writer
        get_stream_writer()(event)
    except Exception:  # noqa: BLE001 - 진행 표시는 실패해도 본 기능에 영향 없음
        pass


def _invoke_text(llm: BaseChatModel, prompt, meter: Meter | None = None, purpose: str = "", **kwargs) -> str:
    if meter is None:
        return (prompt | llm | StrOutputParser()).invoke(kwargs)
    with meter.llm(purpose):
        return (prompt | llm | StrOutputParser()).invoke(kwargs)


# --- 프롬프트 1 ---------------------------------------------------------------------
def extract_queries(llm: BaseChatModel, item_text: str, question: str, max_queries: int,
                    meter: Meter | None = None) -> list[str]:
    raw = _invoke_text(llm, QUERY_EXTRACTION, meter, "queries", item_text=item_text, question=question,
                       max_queries=max_queries)
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
                  max_selected: int, meter: Meter | None = None) -> Callable[[list[dict]], list[int]]:
    def select(cands: list[dict]) -> list[int]:
        labeled = {f"c{i + 1}": c for i, c in enumerate(cands)}
        listing = "\n".join(f"{k}. {c['title']} — {c['summary'][:200]}" for k, c in labeled.items())
        raw = _invoke_text(llm, CANDIDATE_SELECTION, meter, "select", disease_name=disease_name,
                           question=question, query=query, candidates=listing, max_selected=max_selected)
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
        meter: Meter = state.get("meter") or Meter()
        disease = svc.diseases[did]
        meta_doc = svc.store.get_meta(did)
        if meta_doc is None:
            raise IndexMissingError("메타데이터 색인이 없습니다.")

        policy = svc.opt("collect_policy", "background")
        have_docs = svc.store.docs_count(did) >= int(svc.opt("min_docs_for_fast", 1))
        queries, collect_log, warnings, pool = [], [], [], None
        if policy == "background" and not have_docs and svc.collector is not None:
            # 처음 묻는 감염병: LLM이 필요 없는 항목 자체 문서만 바로 색인하고 답한다.
            # 검색어 추출·선별(LLM 여러 번)이 드는 추가 수집은 답한 뒤 배경에서 (실측: 수집이 시간 대부분)
            with meter.stage("collect"):
                try:
                    collect_log = _collect(svc, disease, question, [], meter)
                except Exception:  # noqa: BLE001
                    log.exception("항목 문서 수집 실패")
            have_docs = svc.store.docs_count(did) >= int(svc.opt("min_docs_for_fast", 1))
        fast = policy in ("when_needed", "background") and have_docs and svc.collector is not None
        report = None
        if fast:
            meter.note(f"수집 생략 ({'배경 수집' if policy == 'background' else '이미 모은 문서 사용'})")
            report = svc.background_log.pop(did, None)
            if report:      # 지난 질문 뒤 배경에서 한 수집 결과를 이번 진행 상태에 보여 준다
                for msg in describe_background(report):
                    _emit({"stage": "collect", "message": msg})
            if did in svc.background:
                _emit({"stage": "collect", "message": "배경에서 문서를 모으는 중입니다 (다음 답변부터 반영)"})
            _emit({"stage": "collect", "message": "이미 모은 문서로 바로 답합니다"})
        else:
            with meter.stage("queries"):
                _emit({"stage": "queries", "message": "검색어를 추출하는 중"})
                own_job = None
                pages_before = _page_files(svc)
                pool = ThreadPoolExecutor(max_workers=1) if svc.opt("parallel_collect", True) and svc.collector else None
                if pool:   # 항목 자체 문서는 검색어와 상관없으므로 검색어 추출과 동시에 받기 시작
                    own_job = pool.submit(contextvars.copy_context().run, _fetch_own_page, svc, did)
                queries = extract_queries(svc.helper_llm, meta_doc.page_content, question,
                                          svc.settings["max_queries"], meter)
                _emit({"stage": "queries", "message": f"검색어: {', '.join(queries) or '(없음)'}"})
            if svc.collector is not None:
                with meter.stage("collect"):
                    try:
                        collect_log = _collect(svc, disease, question, queries, meter,
                                               own_job.result() if own_job else None, pages_before)
                    except Exception:  # noqa: BLE001 - 수집 실패 시 이미 색인된 문서로 계속
                        log.exception("외부 문서 수집 실패")
                        warnings.append("외부 문서를 수집하지 못해 이미 색인된 문서로 답변합니다.")
                        _emit({"stage": "collect", "message": warnings[-1], "level": "warning"})
            if pool:
                pool.shutdown(wait=False)

        with meter.stage("search"):
            _emit({"stage": "search", "message": "근거를 검색하는 중"})
            meter.count("embed.query")
            hits = svc.store.search_docs(question, did)
            evidence = build_evidence(meta_doc, hits)
        if fast and policy == "background":
            if start_background_collect(svc, did, question):
                _emit({"stage": "collect", "message": "이 질문에 맞는 추가 문서는 답한 뒤 배경에서 모읍니다 "
                                                      "(검색어 추출 → 후보 선별 → 색인, 다음 답변에 결과 표시)"})
        return {"queries": queries, "collect_log": collect_log, "warnings": warnings, "evidence": evidence,
                "meter": meter, "background_report": report or {}}
    return retrieve


def _fetch_own_page(svc: RagServices, did: str) -> dict | None:
    """항목 자체 위키 문서 본문 (색인 전이면 받아 둠). 색인은 _collect가 순서대로 한다."""
    own = svc.wiki_titles.get(did, {})
    pid = int(own.get("pageid") or 0)
    if not pid or svc.store.is_indexed(did, f"wiki-{pid}"):
        return None
    return {"cached": svc.collector.has_page(pid), "page": svc.collector.get_page(pid, own["title"])}


def _page_files(svc: RagServices) -> set[str]:
    return {p.name for p in svc.collector.pages_dir.glob("*.json")} if svc.collector else set()


def _collect(svc: RagServices, disease: Disease, question: str, queries: list[str], meter: Meter | None = None,
             own_prefetched: dict | None = None, pages_before: set[str] | None = None) -> list[dict]:
    meter = meter or Meter()
    did, name = disease.disease_id, disease.display_name
    log_rows: list[dict] = []
    own = svc.wiki_titles.get(did, {})
    own_pid = int(own.get("pageid") or 0)
    pages_before = _page_files(svc) if pages_before is None else pages_before

    if own_pid:  # 항목 자체 문서: 선별·길이 등 어떤 제외 규칙보다 우선해 항상 색인
        doc_id = f"wiki-{own_pid}"
        if own_prefetched or not svc.store.is_indexed(did, doc_id):
            got = own_prefetched or _fetch_own_page(svc, did)
            if got:
                _emit({"stage": "collect", "message": f"항목 문서 수집: {own.get('title')}"})
                with svc.index_lock:
                    meter.count("embed.index")
                    n = svc.store.index_page(did, name, got["page"], query=name)
                log_rows.append({"query": name, "kind": "item", "from_cache": got["cached"],
                                 "indexed": [got["page"]["title"]], "chunks": n, "rejected": []})
        if not log_rows:
            log_rows.append({"query": name, "kind": "item", "from_cache": True, "indexed": [], "chunks": 0,
                             "rejected": []})

    def fetch(q: str):
        _emit({"stage": "collect", "message": (f"이미 수집한 검색어: {q}" if svc.collector.is_cached(q)
                                               else f"위키백과 검색: {q}")})
        selector = make_selector(svc.helper_llm, name, question, q, svc.settings["max_selected_per_query"], meter)
        res = svc.collector.collect(q, selector, exclude_pageids={own_pid} if own_pid else None)
        if not res.from_cache:
            meter.count("wiki.search")
        return q, res

    if queries and svc.opt("parallel_collect", True):   # 검색어별 검색·선별·본문 수집을 동시에
        with ThreadPoolExecutor(max_workers=len(queries)) as ex:   # 진행 메시지가 화면에 가도록 문맥 복사
            futures = [ex.submit(contextvars.copy_context().run, fetch, q) for q in queries]
            results = [f.result() for f in futures]
    else:
        results = [fetch(q) for q in queries]
    for q, res in results:    # 색인(임베딩·저장)은 순서대로
        indexed, chunks = [], 0
        for page in res.pages:
            with svc.index_lock:
                if not svc.store.is_indexed(did, page["doc_id"]):
                    meter.count("embed.index")
                    chunks += svc.store.index_page(did, name, page, query=q)
                    indexed.append(page["title"])
        log_rows.append({"query": q, "kind": "query", "from_cache": res.from_cache, "indexed": indexed,
                         "chunks": chunks, "rejected": res.rejected})
    fetched = _page_files(svc) - pages_before
    if fetched:
        meter.count("wiki.fetch", len(fetched))
    new_titles = [t for r in log_rows for t in r["indexed"]]
    _emit({"stage": "collect", "message": f"새로 색인한 문서 {len(new_titles)}건" +
           (f": {', '.join(new_titles)}" if new_titles else "")})
    return log_rows


def start_background_collect(svc: RagServices, did: str, question: str) -> threading.Thread | None:
    """답변은 이미 모은 문서로 바로 하고, 이 질문에 맞는 문서 수집은 뒤에서 (다음 질문부터 반영)."""
    if did in svc.background or svc.collector is None:
        return None
    svc.background.add(did)

    def job():
        report = {"question": question, "queries": [], "rows": [], "error": ""}
        try:
            meta = svc.store.get_meta(did)
            report["queries"] = extract_queries(svc.helper_llm, meta.page_content, question,
                                                svc.settings["max_queries"])
            report["rows"] = _collect(svc, svc.diseases[did], question, report["queries"])
        except Exception as exc:  # noqa: BLE001
            log.exception("배경 수집 실패: %s", did)
            report["error"] = type(exc).__name__
        finally:
            svc.background_log[did] = report
            svc.background.discard(did)
    t = threading.Thread(target=job, name=f"collect-{did}", daemon=True)
    t.start()
    return t


def describe_background(report: dict) -> list[str]:
    """배경 수집 결과 → 진행 상태 줄 (프롬프트 1 검색어, 프롬프트 2 선별 결과, 새로 색인한 문서)."""
    if report.get("error"):
        return [f"지난 질문 뒤 배경 수집이 실패했습니다 ({report['error']}). 이미 모은 문서로 답합니다."]
    rows = [r for r in report.get("rows", []) if r.get("kind") == "query"]
    new = [t for r in report.get("rows", []) for t in r.get("indexed", [])]
    rejected = [t for r in rows for t in r.get("rejected", [])]
    lines = [f"지난 질문 뒤 배경 수집 완료 · 검색어(프롬프트 1): {', '.join(report.get('queries') or []) or '없음'}"]
    lines.append(f"새로 색인한 문서(프롬프트 2 선별): {', '.join(new) if new else '없음 (이미 수집했거나 고른 문서 없음)'}"
                 + (f" · 버린 후보 {len(rejected)}건" if rejected else ""))
    return lines


def make_generate(svc: RagServices):
    def generate(state: RagState) -> RagState:
        meter: Meter = state.get("meter") or Meter()
        _emit({"stage": "generate", "message": "답변을 생성하는 중"})
        evidence = state["evidence"]
        disease = svc.diseases[state["disease_id"]]
        kwargs = {"disease_name": disease.display_name, "question": state["question"],
                  "evidence": format_evidence(evidence)}
        with meter.stage("generate"):
            if svc.opt("stream_answer", True):
                parts = []
                with meter.llm("answer"):
                    for chunk in (ANSWER_GENERATION | svc.llm | StrOutputParser()).stream(kwargs):
                        if chunk:
                            meter.first_token()
                            parts.append(chunk)
                            _emit({"stage": "token", "text": chunk})
                answer = "".join(parts).strip()
            else:
                answer = _invoke_text(svc.llm, ANSWER_GENERATION, meter, "answer", **kwargs).strip()
                meter.first_token()
        nums = cited_numbers(answer, len(evidence))
        cited = [evidence[n - 1] for n in nums]
        return {"answer": answer or NOT_FOUND_TEXT, "cited": cited, "timings": meter.summary()}
    return generate


def build_graph(svc: RagServices):
    g = StateGraph(RagState)
    g.add_node("retrieve", make_retrieve(svc))
    g.add_node("generate", make_generate(svc))
    g.add_edge(START, "retrieve")
    g.add_edge("retrieve", "generate")
    g.add_edge("generate", END)
    g = g.compile()
    g.services = svc       # run()이 답변 캐시를 쓰려고 참조
    return g


def run(graph, question: str, disease_id: str, on_event: Callable[[dict], None] | None = None) -> RagState:
    """진행 이벤트(답변 글자 포함)를 콜백으로 넘기면서 최종 상태를 돌려준다. state["timings"]에 시간 기록."""
    meter = Meter()
    svc: RagServices | None = getattr(graph, "services", None)
    cache, key = (svc.answer_cache if svc else None), ""
    if cache is not None:
        key = AnswerCache.key(disease_id, question, str(svc.settings.get("chat_model", "")), ANSWER_PROMPT_VERSION)
        hit = cache.get(key)
        if hit:
            if on_event:
                on_event({"stage": "cache", "message": "같은 질문의 저장된 답변을 사용합니다"})
            t = meter.summary()
            return {**hit, "question": question, "disease_id": disease_id, "timings": {**t, "cache_hit": True}}
    final: RagState = {}
    for mode, chunk in graph.stream({"question": question, "disease_id": disease_id, "meter": meter},
                                    stream_mode=["custom", "values"]):
        if mode == "custom" and on_event:
            on_event(chunk)
        elif mode == "values":
            final = chunk
    final = {k: v for k, v in final.items() if k != "meter"}
    final["timings"] = final.get("timings") or meter.summary()
    if cache is not None and final.get("answer") and not final.get("warnings"):
        cache.put(key, {k: final[k] for k in ("answer", "cited", "evidence", "queries") if k in final})
    return final
