"""조건 기반 항목 탐색 (F2011).

1) 조건에서 급수처럼 정확히 일치해야 하는 값을 규칙으로 뽑아 메타데이터 필터로 건다 (Self-Query 경량형).
2) 메타데이터 컬렉션에서 의미 유사도로 후보를 넉넉히 가져온다.
3) LLM이 후보 정보만 보고 조건을 모두 만족하는 항목만 고른다.
"""
from __future__ import annotations

import logging
import re

from langchain_core.language_models import BaseChatModel
from langchain_core.output_parsers import StrOutputParser

from rag.parsing import parse_json
from rag.prompts import CONDITION_FILTER
from vectorstore.store import DiseaseStore

log = logging.getLogger("kdca_rag")
_GRADE_RE = re.compile(r"제?\s*([1-4])\s*급")


def grade_filter(condition: str) -> dict | None:
    grades = sorted({int(g) for g in _GRADE_RE.findall(condition)})
    if not grades:
        return None
    return {"grade": grades[0]} if len(grades) == 1 else {"grade": {"$in": grades}}


def find_diseases(condition: str, store: DiseaseStore, llm: BaseChatModel, k: int) -> list[dict]:
    where = grade_filter(condition)
    cands = store.search_meta(condition, k=k, where=where)
    if not cands:
        return []
    by_id = {d.metadata["disease_id"]: d for d in cands}
    listing = "\n\n".join(f"id: {i}\n{d.page_content}" for i, d in by_id.items())
    raw = (CONDITION_FILTER | llm | StrOutputParser()).invoke({"condition": condition, "candidates": listing})
    try:
        data = parse_json(raw)
        matches = data.get("matches", []) if isinstance(data, dict) else data
    except ValueError:
        log.warning("조건 탐색 결과를 파싱하지 못함")
        return []
    out = []
    for m in matches:
        if not isinstance(m, dict) or m.get("id") not in by_id:
            continue  # 후보에 없던 ID는 버림 (조건과 무관한 항목이 섞이지 않게)
        md = by_id[m["id"]].metadata
        out.append({"disease_id": m["id"], "name": md["disease_name"], "grade": md["grade"],
                    "group": md.get("group", ""), "reason": str(m.get("reason", "")).strip()})
    return out
