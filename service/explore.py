"""조건 기반 항목 탐색 (F2011).

1) 조건에서 급수·신고 범위처럼 정확히 일치해야 하는 값을 규칙으로 뽑아 메타데이터 필터로 건다 (Self-Query 경량형).
2) 메타데이터 컬렉션에서 의미 유사도로 후보를 넉넉히 가져온다.
3) LLM이 후보 정보만 보고 조건을 모두 만족하는 항목만 고른다.
4) LLM이 고른 결과를 1)의 조건과 다시 대조해 어긋나는 항목은 버린다 (조건과 맞지 않는 항목이 섞이지 않게).
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
# 신고 범위: '병원체보유자도 신고', '의사환자는 신고하지 않는', '환자만'은 범위 판단이 애매해 다루지 않는다
_SCOPE_RE = re.compile(r"(의사환자|병원체보유자|(?<!의사)환자)\s*(?:도|는|은|를|을|까지)?\s*"
                       r"(신고\s*(?:하지\s*않|안\s*하|대상이\s*아니|제외)|제외|빠지|신고|포함)")


def grade_filter(condition: str) -> dict | None:
    grades = sorted({int(g) for g in _GRADE_RE.findall(condition)})
    if not grades:
        return None
    return {"grade": grades[0]} if len(grades) == 1 else {"grade": {"$in": grades}}


def structured_conditions(condition: str) -> dict:
    """{'grade': [3], 'scope': {'병원체보유자': True}} - 데이터로 정확히 확인할 수 있는 조건."""
    out: dict = {}
    grades = sorted({int(g) for g in _GRADE_RE.findall(condition)})
    if grades:
        out["grade"] = grades
    for who, verb in _SCOPE_RE.findall(condition):
        out.setdefault("scope", {})[who] = not re.search(r"않|안\s*하|아니|제외|빠지", verb)
    return out


def where_filter(cons: dict) -> dict | None:
    parts = []
    if cons.get("grade"):
        g = cons["grade"]
        parts.append({"grade": g[0]} if len(g) == 1 else {"grade": {"$in": g}})
    parts += [{f"scope_{who}": want} for who, want in cons.get("scope", {}).items()]
    if not parts:
        return None
    return parts[0] if len(parts) == 1 else {"$and": parts}


def satisfies(md: dict, cons: dict) -> bool:
    if cons.get("grade") and int(md.get("grade", 0)) not in cons["grade"]:
        return False
    return all(md.get(f"scope_{who}") == want for who, want in cons.get("scope", {}).items())


def find_diseases(condition: str, store: DiseaseStore, llm: BaseChatModel, k: int) -> list[dict]:
    cons = structured_conditions(condition)
    where = where_filter(cons)
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
        if not satisfies(md, cons):
            log.info("조건과 어긋난 LLM 선택을 버림: %s", m["id"])
            continue
        out.append({"disease_id": m["id"], "name": md["disease_name"], "grade": md["grade"],
                    "group": md.get("group", ""), "reason": str(m.get("reason", "")).strip()})
    return out
