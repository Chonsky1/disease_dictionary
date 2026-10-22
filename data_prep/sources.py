"""출처 등록부(config/sources.json)에서 화면 캡션용 이용 조건 문구를 만든다 (NF2007)."""
from __future__ import annotations

import json
from functools import lru_cache

from data_prep import settings as paths

SHORT = {"공공누리 제4유형": "공공누리 제4유형(출처표시·비상업·변경금지)"}
DERIVED = "원자료 수치를 바꾸지 않고 집계·시각화"


@lru_cache(maxsize=1)
def registry() -> dict[str, dict]:
    data = json.loads((paths.CONFIG / "sources.json").read_text(encoding="utf-8"))
    return {s["id"]: s for s in data["sources"]}


def license_label(source_id: str) -> str:
    """'공공누리 제4유형(출처표시·비상업·변경금지)'처럼 짧게. 모르는 ID면 빈 문자열."""
    lic = registry().get(source_id, {}).get("license", "")
    for key, short in SHORT.items():
        if lic.startswith(key):
            return short
    return lic.split(" (")[0] if lic and not lic.startswith("미확인") else ""


def stats_terms() -> str:
    """감염병포털 통계 화면 캡션 꼬리: 유형 + 집계 문구."""
    lab = license_label("age_sex_incidence")
    return f"{lab} · {DERIVED}" if lab else DERIVED
