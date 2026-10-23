"""여행 전 안내 (F2010 도메인 특화 기능).

나라 팝업에서 그 나라가 검역관리지역으로 지정된 검역감염병마다
  1) 항목 메타데이터(감염병포털 법정감염병 목록)
  2) 검역관리지역 지정 국가 문서(공공데이터 API, data_prep/public_docs.py)
  3) 이미 색인된 외부 문서 중 '전파 경로·예방'과 가까운 청크 (disease_id 필터)
를 근거로 모아, 감염병별 전파 경로·예방·귀국 후 할 일을 [근거 n]과 함께 정리한다.
답변 생성과 같은 규칙: 근거에 없는 내용은 쓰지 않고, 본문에 인용된 근거만 화면에 남긴다.
"""
from __future__ import annotations

from langchain_core.output_parsers import StrOutputParser

from rag.graph import format_evidence
from rag.parsing import cited_numbers
from rag.prompts import TRAVEL_BRIEF
from vectorstore.store import text_hash

MAX_DISEASES = 6
DOCS_PER_DISEASE = 2


def _item(doc, kind: str | None = None) -> dict:
    m = doc.metadata
    kind = kind or m.get("source_type", "wiki")
    return {"chunk_id": doc.id, "source_type": kind,
            "source_label": "항목 메타데이터" if kind == "meta" else m.get("source_label", "위키백과"),
            "title": m.get("title", ""), "section": m.get("section", ""), "url": m.get("source_url", ""),
            "wiki_url": m.get("wiki_url", ""), "text": doc.page_content}


def gather_evidence(store, disease_ids: list[str], names: dict[str, str]) -> list[dict]:
    """감염병마다 메타데이터 → 검역관리지역 문서 → 위키 청크 최대 2개. 청크 ID·본문 해시로 중복 제거."""
    items, seen_ids, seen_hash = [], set(), set()

    def add(it: dict) -> bool:
        h = text_hash(it["text"])
        if it["chunk_id"] in seen_ids or h in seen_hash:
            return False
        seen_ids.add(it["chunk_id"])
        seen_hash.add(h)
        items.append(it)
        return True

    for did in disease_ids[:MAX_DISEASES]:
        meta = store.get_meta(did)
        if meta is None:
            continue
        add(_item(meta, "meta"))
        for doc in store.get_docs(did, "public_api"):
            add(_item(doc))
        wiki = 0
        for doc in store.search_docs(f"{names.get(did, '')} 전파 경로 예방 여행", did, k=DOCS_PER_DISEASE + 3):
            if wiki < DOCS_PER_DISEASE and doc.metadata.get("source_type", "wiki") == "wiki" and add(_item(doc)):
                wiki += 1
    for i, it in enumerate(items, start=1):
        it["n"] = i
    return items


def travel_brief(country_name: str, as_of: str, disease_ids: list[str], rt) -> dict:
    """{'answer', 'cited', 'evidence'}. 근거가 하나도 없으면 LLM을 부르지 않는다."""
    names = {did: rt.by_id[did].display_name for did in disease_ids if did in rt.by_id}
    evidence = gather_evidence(rt.store, [d for d in disease_ids if d in names], names)
    if not evidence:
        return {"answer": "제공된 문서에서 확인되지 않습니다.", "cited": [], "evidence": []}
    answer = (TRAVEL_BRIEF | rt.services.llm | StrOutputParser()).invoke(
        {"country": country_name, "as_of": as_of, "evidence": format_evidence(evidence)}).strip()
    cited = [evidence[n - 1] for n in cited_numbers(answer, len(evidence))]
    return {"answer": answer, "cited": cited, "evidence": evidence}
