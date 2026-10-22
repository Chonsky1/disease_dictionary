"""연령별·시기별 통계 묶기 → data/processed/age_stats.json (화면의 '연령별 알아보기'가 읽는 파일).

python -m scripts.build_age_stats                  # data/raw/stats/ 안의 CSV·xlsx 전부
python -m scripts.build_age_stats --dir 다른폴더

감염병포털에서 내려받은 파일을 이름 그대로 data/raw/stats/에 넣으면 된다 (종류는 내용으로 판별).
결과 보고: reports/age_stats_report.md (파일별 처리 결과, 법정감염병 목록과 연결 안 된 이름)
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from data_prep import settings as paths
from data_prep.age_stats import build_age_stats, load_columns, save
from data_prep.diseases import load_diseases

STATS_DIR = paths.RAW / "stats"
OUT = paths.PROCESSED / "age_stats.json"
KIND_LABEL = {"basic_csv": "전수감시 성별·연령별 (CSV)", "flu_csv": "인플루엔자 의사환자분율 (CSV)",
              "age_sex": "표본감시 연령별·성별", "sari": "중증급성호흡기감염증 절기별 연령별",
              "year_week": "연도별 주간 의사환자분율", "pathogen_weekly": "병원체 주별 검출",
              "unsupported": "사용 안 함", "error": "오류"}


def _show(p: Path) -> str:
    try:
        return str(p.relative_to(paths.ROOT))
    except ValueError:
        return str(p)


def find_files(folder: Path) -> list[Path]:
    """하위 폴더까지 (압축을 풀면 폴더째 들어가는 경우가 많음). 엑셀 임시 파일(~$…)과 macOS 부속 파일은 제외."""
    if not folder.exists():
        return []
    return sorted(p for p in folder.rglob("*")
                  if p.is_file() and p.suffix.lower() in {".csv", ".xlsx"}
                  and not p.name.startswith(("~$", "._")) and "__MACOSX" not in p.parts)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", type=Path, default=STATS_DIR)
    args = ap.parse_args(argv)
    paths.ensure_dirs()
    if args.dir == STATS_DIR:
        STATS_DIR.mkdir(parents=True, exist_ok=True)
    files = find_files(args.dir)
    if not files:
        inside = sorted(p.name + ("/" if p.is_dir() else "") for p in args.dir.iterdir()) if args.dir.exists() else []
        zips = [n for n in inside if n.lower().endswith(".zip")]
        raise SystemExit(f"[x] {_show(args.dir.resolve())}에서 CSV·xlsx 파일을 찾지 못했습니다 (하위 폴더 포함).\n"
                         f"    지금 폴더 안: {', '.join(inside[:10]) or '비어 있음'}\n"
                         + ("    zip 파일은 압축을 먼저 풀어 주세요.\n" if zips else "")
                         + "    감염병포털에서 내려받은 통계 파일을 이 폴더에 넣고 다시 실행하세요.")
    print(f"[stats] {_show(args.dir)}에서 파일 {len(files)}개 발견")
    cols = load_columns(paths.CONFIG / "age_stats_columns.json")
    data, log = build_age_stats(files, load_diseases(paths.DISEASES_CSV), cols)
    save(OUT, data)

    n_used = sum(r["used"] for r in log)
    sets = data["age_sets"]
    print(f"[stats] 파일 {len(files)}개 중 {n_used}개 사용 → {_show(OUT)}")
    print(f"        연령 분포: 전수감시 {len(sets['notifiable']['diseases'])}종, 성매개 표본감시 {len(sets['sti']['diseases'])}종, "
          f"중증급성호흡기 {len(data['sari_by_age']['seasons'])}절기 · 유행 시기 자료 {len(data['trends'])}개")

    unlinked = sorted({f"{s['label']}: {n}" for s in sets.values() for n, v in s["diseases"].items() if not v["disease_id"]}
                      | {f"{t['label']}: {n}" for t in data["trends"] for n, v in t["items"].items()
                         if not v["disease_id"] and t["id"] != "sari" and n != "전체"})
    lines = ["# 연령별·시기별 통계 처리 결과", "", f"- 생성: {data['generated_at']}", f"- 파일 {len(files)}개 중 {n_used}개 사용", "",
             "| 파일 | 종류 | 사용 | 내용 |", "| --- | --- | --- | --- |",
             *[f"| {r['file']} | {KIND_LABEL.get(r['kind'], r['kind'])} | {'O' if r['used'] else 'X'} | {r['note']} |" for r in log],
             "", "## 전수감시 열 이름", "",
             ("- 포털 머리글로 확인됨" if cols.get("verified") else
              "- **추정** (config/age_stats_columns.json). 포털 화면 머리글과 비교한 뒤 verified를 true로 바꾸세요."),
             f"- 조회 기간: {cols.get('period') or '미기재 (config/age_stats_columns.json의 period)'}",
             "", "## 법정감염병 목록과 연결 안 된 이름 (질문하기 버튼 없이 표시)", "",
             *([f"- {u}" for u in unlinked] or ["- 없음"])]
    report = paths.REPORTS / "age_stats_report.md"
    report.write_text("\n".join(lines) + "\n", encoding="utf-8")
    errors = [r for r in log if r["kind"] == "error"]
    for r in errors:
        print(f"[!] {r['file']}: {r['note']}")
    if not cols.get("verified"):
        print("[i] 전수감시 CSV의 열 이름은 추정입니다 (config/age_stats_columns.json, 화면에도 '추정'으로 표시).")
    print(f"[report] {_show(report)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
