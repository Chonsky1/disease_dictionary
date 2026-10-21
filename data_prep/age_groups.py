"""데이터셋마다 다른 연령 구간을 서비스 공통 연령대로 맞춘다.

공통 연령대는 config/age_groups.json에서 정한다. 원본 구간이 공통 연령대 두 개에
걸치면(예: 5-9세 → 0-6, 7-12) 겹치는 비율이 큰 쪽에 넣고 exact=False로 표시한다.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

TOTAL_WORDS = ("계", "합계", "전체", "소계", "total")
UNKNOWN_WORDS = ("미상", "불명", "모름", "unknown")
OPEN_UPPER = 120  # '65세 이상'의 상한으로 쓰는 값


@dataclass(frozen=True)
class AgeBand:
    lo: int
    hi: int  # 포함 상한


@dataclass(frozen=True)
class CommonGroup:
    id: str
    label: str
    lo: int
    hi: int


def load_common_groups(path: Path) -> list[CommonGroup]:
    cfg = json.loads(Path(path).read_text(encoding="utf-8"))
    return [CommonGroup(g["id"], g["label"], g["lo"], g["hi"] if g["hi"] is not None else OPEN_UPPER)
            for g in cfg["common_groups"]]


def parse_age_label(label: str) -> AgeBand | str | None:
    """'0-6세', '0~6세', '65세 이상', '65+', '1세 미만', '0세' 등을 AgeBand로.

    합계 행이면 'total', 미상이면 'unknown', 해석 불가면 None.
    """
    s = str(label).strip().replace(" ", "")
    if not s:
        return None
    low = s.lower()
    if any(w in low for w in TOTAL_WORDS):
        return "total"
    if any(w in low for w in UNKNOWN_WORDS):
        return "unknown"
    s = s.replace("세", "").replace("살", "").replace("만", "")
    if m := re.fullmatch(r"(\d+)(?:이상|\+|~)", s):
        return AgeBand(int(m.group(1)), OPEN_UPPER)
    if m := re.fullmatch(r"(\d+)미", s) or re.fullmatch(r"(\d+)미달", s):
        return AgeBand(0, int(m.group(1)) - 1)
    if m := re.fullmatch(r"(\d+)[-~–](\d+)", s):
        lo, hi = int(m.group(1)), int(m.group(2))
        return AgeBand(lo, hi) if lo <= hi else None
    if m := re.fullmatch(r"(\d+)", s):
        return AgeBand(int(m.group(1)), int(m.group(1)))
    return None


_AGE_MARKER = re.compile(r"세|살|이상|미만|\+|~|\d\s*[-–]\s*\d")


def is_age_label(label) -> bool:
    """열 역할 추정용 엄격 판정: '3'처럼 숫자만 있는 값(건수)은 연령 라벨로 보지 않는다."""
    s = str(label).strip()
    return bool(_AGE_MARKER.search(s)) and isinstance(parse_age_label(s), AgeBand)


def map_to_common(band: AgeBand, groups: list[CommonGroup]) -> tuple[str | None, float, bool]:
    """(공통 연령대 id, 겹침 비율, 정확히 포함되는지)."""
    span = band.hi - band.lo + 1
    best_id, best_overlap = None, 0
    for g in groups:
        overlap = min(band.hi, g.hi) - max(band.lo, g.lo) + 1
        if overlap > best_overlap:
            best_id, best_overlap = g.id, overlap
    if best_id is None:
        return None, 0.0, False
    ratio = best_overlap / span
    return best_id, round(ratio, 3), ratio == 1.0
