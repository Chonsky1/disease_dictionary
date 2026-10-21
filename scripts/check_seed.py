"""1일차 16시 관문 점검: 시드 항목 메타데이터가 도메인 조건을 만족하는가.

실행: python -m scripts.check_seed
결과: reports/seed_check.md, 관문 미통과 시 종료 코드 1
"""
from __future__ import annotations

import csv
import re
import sys
from collections import Counter
from datetime import date

from data_prep import settings
from data_prep.names import name_key

MIN_ITEMS = 100
REQUIRED = ("disease_id", "name_ko", "grade", "surveillance")
RECOMMENDED = ("kcd", "pathogen_type", "transmission", "report_deadline")


def main(path=settings.SEED / "diseases_seed.csv") -> int:
    settings.ensure_dirs()
    with open(path, encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))

    problems, warnings = [], []
    n = len(rows)
    if n < MIN_ITEMS:
        problems.append(f"항목 {n}건 < {MIN_ITEMS}건 (1~3급만으로는 부족, 4급 포함 필요)")

    for col in REQUIRED:
        missing = [r.get("disease_id") or f"행{i + 2}" for i, r in enumerate(rows) if not (r.get(col) or "").strip()]
        if missing:
            problems.append(f"필수 열 '{col}' 비어 있음: {len(missing)}건 (예: {', '.join(missing[:5])})")

    bad_ids = [r["disease_id"] for r in rows if not re.fullmatch(r"inf_\d{4}", r.get("disease_id", ""))]
    if bad_ids:
        problems.append(f"ID 형식(inf_0000) 위반: {bad_ids[:5]}")
    dup_ids = [k for k, c in Counter(r["disease_id"] for r in rows).items() if c > 1]
    if dup_ids:
        problems.append(f"중복 ID: {dup_ids}")
    dup_names = [k for k, c in Counter(name_key(r["name_ko"]) for r in rows).items() if c > 1]
    if dup_names:
        problems.append(f"정규화 후 중복 이름: {dup_names}")

    bad_grade = [r["name_ko"] for r in rows if r.get("grade") not in {"1", "2", "3", "4"}]
    if bad_grade:
        problems.append(f"grade가 1~4가 아님: {bad_grade[:5]}")
    mismatch = [r["name_ko"] for r in rows
                if (r.get("grade") == "4") != (r.get("surveillance") == "표본")]
    if mismatch:
        warnings.append(f"급수와 감시 유형 불일치(4급=표본 기대): {mismatch[:5]}")

    fill = {col: sum(1 for r in rows if (r.get(col) or "").strip()) for col in RECOMMENDED}
    for col, c in fill.items():
        if n and c < n:
            warnings.append(f"권장 열 '{col}' 채움률 {c}/{n} ({c / n:.0%})")

    by_grade = Counter(r.get("grade") for r in rows)
    by_pathogen = Counter((r.get("pathogen_type") or "(미기입)") for r in rows)

    status = "통과" if not problems else "미통과"
    lines = [
        f"# 시드 점검 결과 ({date.today()})", "",
        f"- 관문: **{status}**  (항목 {n}건 / 기준 {MIN_ITEMS}건)",
        "", "## 급수별 건수", "", "| 급수 | 건수 |", "| --- | --- |",
        *[f"| {g}급 | {by_grade.get(g, 0)} |" for g in ("1", "2", "3", "4")],
        "", "## 병원체 유형별 건수", "", "| 유형 | 건수 |", "| --- | --- |",
        *[f"| {k} | {v} |" for k, v in by_pathogen.most_common()],
        "", "## 문제 (관문 차단)", "", *([f"- {p}" for p in problems] or ["- 없음"]),
        "", "## 경고 (보완 권장)", "", *([f"- {w}" for w in warnings] or ["- 없음"]),
    ]
    out = settings.REPORTS / "seed_check.md"
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"[seed] {status}: {n}건, 문제 {len(problems)}개, 경고 {len(warnings)}개 → {out.relative_to(settings.ROOT)}")
    return 0 if not problems else 1


if __name__ == "__main__":
    sys.exit(main())
