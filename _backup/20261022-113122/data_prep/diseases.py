"""법정감염병 항목 메타데이터 로드와 색인 텍스트 구성 (F2001).

입력: data/seed/infectious_diseases.csv (감염병포털 법정감염병 목록, 133건)
열: 급수, 감염병명, KCD코드, 환자, 의사환자, 병원체보유자, 담당부서
"""
from __future__ import annotations

import csv
import re
from dataclasses import dataclass, field
from pathlib import Path

PORTAL_URL = "https://dportal.kdca.go.kr/pot/ii/sttyInftnsds/sttyInftnsds.do"
PORTAL_LABEL = "질병관리청 감염병포털 법정감염병 목록"

# 급수별 법적 기준 (감염병의 예방 및 관리에 관한 법률, 질병관리청 감염병포털 안내)
GRADE_RULES = {
    "1": {"deadline": "즉시", "surveillance": "전수감시",
          "desc": "생물테러감염병 또는 치명률이 높거나 집단 발생 우려가 커서 발생 또는 유행 즉시 신고하고 음압격리와 같은 높은 수준의 격리가 필요한 감염병"},
    "2": {"deadline": "24시간 이내", "surveillance": "전수감시",
          "desc": "전파가능성을 고려하여 발생 또는 유행 시 24시간 이내에 신고하여야 하고 격리가 필요한 감염병"},
    "3": {"deadline": "24시간 이내", "surveillance": "전수감시",
          "desc": "발생을 계속 감시할 필요가 있어 발생 또는 유행 시 24시간 이내에 신고하여야 하는 감염병"},
    "4": {"deadline": "7일 이내", "surveillance": "표본감시",
          "desc": "제1급~제3급 감염병 외에 유행 여부를 조사하기 위하여 표본감시 활동이 필요한 감염병"},
}

SCOPE_COLUMNS = ("환자", "의사환자", "병원체보유자")
_GROUP_RE = re.compile(r"^\((?P<group>[^)]+)\)\s*(?P<name>.+)$")
_ABBR_RE = re.compile(r"\(([A-Za-z][A-Za-z0-9\- ]*)\)")


@dataclass
class Disease:
    disease_id: str
    grade: str
    name: str                 # 원문 이름 (예: "(장관감염증) 노로바이러스 감염증")
    display_name: str         # 분류 접두어를 뗀 이름 (예: "노로바이러스 감염증")
    group: str                # 분류 접두어 (예: "장관감염증"), 없으면 ""
    kcd: str
    scope: dict[str, bool]
    department: str
    abbreviations: list[str] = field(default_factory=list)

    @property
    def rule(self) -> dict:
        return GRADE_RULES[self.grade]

    def label(self) -> str:
        return f"{self.display_name} (제{self.grade}급)"

    def wiki_title_candidates(self) -> list[str]:
        """위키백과 제목 후보: 원래 이름 → 괄호 뺀 이름 → 띄어쓰기 뺀 이름 → 영문 약어."""
        base = self.display_name
        no_paren = re.sub(r"\s*\(.*?\)\s*", " ", base).strip()
        out = [base, no_paren, no_paren.replace(" ", ""), *self.abbreviations]
        return list(dict.fromkeys(t for t in out if t))


def _scope(row: dict) -> dict[str, bool]:
    return {c: (row.get(c) or "").strip().upper() == "O" for c in SCOPE_COLUMNS}


def load_diseases(path: Path) -> list[Disease]:
    with open(path, encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))
    out = []
    for i, row in enumerate(rows, start=1):
        name = row["감염병명"].strip()
        grade = row["급수"].strip()
        if grade not in GRADE_RULES:
            raise ValueError(f"{i + 1}행 '{name}': 급수 '{grade}'를 해석할 수 없습니다.")
        m = _GROUP_RE.match(name)
        group, display = (m.group("group"), m.group("name").strip()) if m else ("", name)
        out.append(Disease(
            disease_id=f"inf_{i:04d}", grade=grade, name=name, display_name=display, group=group,
            kcd=(row.get("KCD코드") or "").strip(), scope=_scope(row),
            department=(row.get("담당부서") or "").strip(),
            abbreviations=_ABBR_RE.findall(display),
        ))
    return out


def _yes_no(v: bool) -> str:
    return "포함" if v else "제외"


def build_index_text(d: Disease, summary: str | None = None) -> str:
    """항목 1건 = 검색 텍스트 1개. 사용자가 감염병을 찾는 기준(이름, 급수, 신고 기준, 분류, 개요)을 모두 넣는다."""
    r = d.rule
    scope = ", ".join(f"{k} {_yes_no(v)}" for k, v in d.scope.items())
    lines = [
        f"[법정감염병] {d.display_name}",
        f"급수: 제{d.grade}급 감염병 ({r['surveillance']})",
        f"급수 기준: {r['desc']}",
        f"신고 시기: {r['deadline']}",
        f"신고 범위: {scope}",
        f"KCD 코드: {d.kcd or '목록에 없음'}",
    ]
    if d.group:
        lines.append(f"분류: {d.group}")
    if d.abbreviations:
        lines.append(f"약어: {', '.join(d.abbreviations)}")
    lines.append(f"질병관리청 담당부서: {d.department}")
    if summary:
        lines.append(f"개요(위키백과 요약): {summary.strip()}")
    return "\n".join(lines)


def build_metadata(d: Disease, wiki: dict | None = None) -> dict:
    """Chroma 메타데이터 (값은 str/int/float/bool만, None 금지)."""
    meta = {
        "disease_id": d.disease_id, "disease_name": d.display_name, "grade": int(d.grade),
        "group": d.group, "kcd": d.kcd, "department": d.department,
        "surveillance": d.rule["surveillance"], "report_deadline": d.rule["deadline"],
        **{f"scope_{k}": v for k, v in d.scope.items()},
        "source_type": "meta", "source_label": "항목 메타데이터",
        "title": d.display_name, "source_url": PORTAL_URL,
        "wiki_title": "", "wiki_url": "",
    }
    if wiki and wiki.get("title"):
        meta["wiki_title"] = wiki["title"]
        meta["wiki_url"] = wiki.get("url", "")
    return meta
