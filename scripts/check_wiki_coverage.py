"""시드 감염병마다 한국어 위키백과 문서가 있는지, 길이는 어떤지 확인.

실행: python -m scripts.check_wiki_coverage
결과: data/processed/wiki_coverage.csv, reports/wiki_coverage.md
"""
from __future__ import annotations

import csv
import math
import statistics
import sys

from data_prep import settings
from data_prep.wiki import WikiClient

# 위키텍스트 바이트 → 본문 글자 수 대략 환산 (한글 UTF-8 3바이트, 마크업 제외 감안).
# 정확한 값은 2일차 수집기로 본문을 받아 다시 계산한다.
BYTES_PER_CHAR = 3.5


def estimate_chunks(chars: int, size: int, overlap: int) -> int:
    if chars <= size:
        return 1
    return math.ceil((chars - overlap) / (size - overlap))


def main() -> int:
    settings.ensure_dirs()
    ua = settings.env("WIKI_USER_AGENT", required=True)
    client = WikiClient(ua, min_interval=0.5)

    with open(settings.SEED / "diseases_seed.csv", encoding="utf-8-sig", newline="") as f:
        seeds = list(csv.DictReader(f))
    titles = [(s.get("wiki_title") or s["name_ko"]).strip() for s in seeds]
    result = {}
    for t in titles:  # page.exists()로 하나씩 확인 (리다이렉트 자동 해석)
        r = client.resolve(t)
        result[t] = ({"exists": True, "resolved_title": r["title"], "redirected": r["redirected"],
                      "length": r["length"], "pageid": r["pageid"]} if r else {"exists": False})

    rows = []
    for s, t in zip(seeds, titles):
        r = result.get(t, {})
        hint = "" if r.get("exists") else "; ".join(client.search_titles(s["name_ko"], limit=3))
        est = round(r["length"] / BYTES_PER_CHAR) if r.get("length") else None
        rows.append({"disease_id": s["disease_id"], "name_ko": s["name_ko"], "grade": s.get("grade"),
                     "query_title": t, "exists": r.get("exists", False),
                     "resolved_title": r.get("resolved_title") or "", "redirected": r.get("redirected", False),
                     "wikitext_bytes": r.get("length") or "", "est_chars": est or "",
                     "est_chunks_1000_200": estimate_chunks(est, 1000, 200) if est else "",
                     "search_hint": hint})

    out_csv = settings.PROCESSED / "wiki_coverage.csv"
    with open(out_csv, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)

    n = len(rows)
    found = [r for r in rows if r["exists"]]
    lengths = sorted(r["est_chars"] for r in found if r["est_chars"])
    q = statistics.quantiles(lengths, n=4) if len(lengths) >= 4 else []
    total_chunks = sum(r["est_chunks_1000_200"] for r in found if r["est_chunks_1000_200"])
    lines = [
        "# 위키백과 커버리지", "",
        f"- 문서 있음: {len(found)}/{n} ({len(found) / n:.0%}), 목표 90% 이상",
        f"- 리다이렉트로 찾은 문서: {sum(1 for r in found if r['redirected'])}건 → wiki_title에 정식 제목 기록 권장",
        f"- 본문 길이(추정 글자 수) 사분위: {', '.join(str(round(x)) for x in q) or '표본 부족'}",
        f"- 1,000자/200자 중복 기준 예상 청크 수(항목 자체 문서만): 약 {total_chunks}개",
        "", "## 문서 없는 항목", "", "| ID | 이름 | 급수 | 검색 후보 |", "| --- | --- | --- | --- |",
        *[f"| {r['disease_id']} | {r['name_ko']} | {r['grade']} | {r['search_hint']} |" for r in rows if not r["exists"]],
    ]
    (settings.REPORTS / "wiki_coverage.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"[wiki] {len(found)}/{n} 문서 확인 → {out_csv.relative_to(settings.ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
