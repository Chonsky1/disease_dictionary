"""검역관리지역 정보 수집 → data/processed/country_watch.json (지도 나라 패널이 읽는 파일).

python -m scripts.fetch_quarantine                      # Open API (config/sources.json의 quarantine_area)
python -m scripts.fetch_quarantine --endpoint https://apis.data.go.kr/…   # 요청주소를 직접 넘기기
python -m scripts.fetch_quarantine --csv 파일.csv        # 파일데이터 CSV로 대신 (API 승인 전·장애 시)
python -m scripts.fetch_quarantine --date 2026-10-22    # 기준일 지정 (기본: 오늘)

결과: data/raw/api/quarantine_area/<날짜>.json (API 원본), data/processed/country_watch.json,
      reports/quarantine_report.md (지정 국가 수, 지도에 못 올린 국가, 목록과 안 맞는 감염병명)
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

from data_prep import settings as paths
from data_prep.diseases import load_diseases
from data_prep.quarantine import (build_watch, from_api_items, from_csv_rows, load_country_codes, save_watch)

WATCH_PATH = paths.PROCESSED / "country_watch.json"


def _show(p: Path) -> str:
    try:
        return str(p.relative_to(paths.ROOT))
    except ValueError:
        return str(p)


def _from_api(max_pages: int, endpoint: str | None) -> tuple[list, str]:
    from data_prep.http_client import PoliteSession
    from data_prep.kdca_api import ApiError, fetch_all, save_raw

    cfg = json.loads((paths.CONFIG / "sources.json").read_text(encoding="utf-8"))
    src = next(s for s in cfg["sources"] if s["id"] == "quarantine_area")
    api = src.setdefault("api", {})
    if endpoint:
        api["endpoint"] = endpoint
    # 정의서 기준 필수 값: JSON 응답(resType=2), 사용 중인 지정만(useYn=Y). 설정 파일이 옛 버전이어도 보장.
    api["params"] = {**{k: v for k, v in api.get("params", {}).items() if k != "type"}, "resType": "2", "useYn": "Y"}
    api.setdefault("page_size", 100)
    ep = str(api.get("endpoint", "TODO"))
    if ep.rstrip("/").endswith("/QuarantineRegion"):
        raise SystemExit("[x] 서비스 기본 주소만 있습니다. 뒤에 오퍼레이션 이름을 붙여야 합니다.\n"
                         f"    예: {ep.rstrip('/')}/<오퍼레이션 이름>\n"
                         "    오퍼레이션 이름은 공공데이터포털 마이페이지 > 활용신청 현황 > 질병관리청_검역관리지역정보 >\n"
                         "    상세기능 목록의 '검역관리지역 조회 서비스(국가별 검역감염병)' 행에 있습니다.")
    if ep.startswith("TODO"):
        raise SystemExit("[x] config/sources.json의 quarantine_area.api.endpoint가 TODO입니다.\n"
                         "    공공데이터포털 마이페이지 > 활용신청 현황 > 질병관리청_검역관리지역정보 > 상세기능에서\n"
                         "    '검역관리지역 조회 서비스(국가별 검역감염병)'의 요청주소를 붙여 넣거나 --endpoint 로 넘기세요.\n"
                         "    API를 아직 못 쓰면 --csv 로 파일데이터를 쓰세요.")
    key = paths.env("DATA_GO_KR_SERVICE_KEY", required=True)
    try:
        items = fetch_all(src, PoliteSession("kdca-rag/0.1", min_interval=0.3), key, max_pages=max_pages)
    except ApiError as exc:
        hint = ("    12(NO_OPENAPI_SERVICE_ERROR)·404면 요청주소의 오퍼레이션 이름을 다시 확인하세요.\n"
                "    102·30번대(인증·미등록 키)면 Decoding 키 사용 여부와 활용신청 승인 상태를 확인하세요.\n"
                "    22번(호출 한도 초과)이면 내일 다시 시도하거나 --csv 파일데이터를 쓰세요.")
        raise SystemExit(f"[x] API 오류: {exc}\n{hint}")
    raw = save_raw("quarantine_area", items, paths.RAW_API, {"endpoint": src["api"]["endpoint"],
                                                            "params": src["api"].get("params", {})})
    print(f"[api] {len(items)}건 수집 → {_show(raw)}")
    return from_api_items(items), "Open API"


def _from_csv(path: Path) -> tuple[list, str]:
    from data_prep.csv_loader import read_table
    header, rows, meta = read_table(path)
    print(f"[csv] {path.name}: {len(rows)}행 (인코딩 {meta['encoding']})")
    return from_csv_rows(header, rows), f"파일데이터 {path.name}"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", type=Path)
    ap.add_argument("--endpoint", help="API 요청주소 (config/sources.json 대신)")
    ap.add_argument("--date", help="기준일 YYYY-MM-DD (이 날짜에 지정 중인 것만)")
    ap.add_argument("--max-pages", type=int, default=50)
    args = ap.parse_args(argv)
    paths.ensure_dirs()

    records, label = _from_csv(args.csv) if args.csv else _from_api(args.max_pages, args.endpoint)
    today = date.fromisoformat(args.date) if args.date else date.today()
    watch, st = build_watch(records, load_diseases(paths.DISEASES_CSV),
                            load_country_codes(paths.CONFIG / "country_names_ko.json"),
                            paths.CONFIG / "name_aliases.json", today, label)
    save_watch(WATCH_PATH, watch)
    print(f"[watch] 전체 {st['records']}건 중 {today} 기준 지정 중 {st['active']}건 → {st['countries']}개 나라 "
          f"(지도 표시 {st['mapped']}개) → {_show(WATCH_PATH)}")

    lines = [f"# 검역관리지역 수집 결과 ({today}, {label})", "",
             f"- 전체 {st['records']}건, 기준일에 지정 중 {st['active']}건, {st['countries']}개 나라",
             f"- 지정 중인 검역감염병 {len(st['diseases'])}종: {', '.join(st['diseases'])}", "",
             "## 지도에 못 올린 국가 (config/country_names_ko.json에 ISO-3 코드 추가)", "",
             *([f"- {c}" for c in st["unmapped_countries"]] or ["- 없음"]), "",
             "## 법정감염병 목록과 안 맞는 감염병명 (질문하기 버튼이 안 생김)", "",
             *([f"- {d}" for d in st["unmatched_diseases"]] or ["- 없음"])]
    report = paths.REPORTS / "quarantine_report.md"
    report.write_text("\n".join(lines) + "\n", encoding="utf-8")
    if st["unmapped_countries"] or st["unmatched_diseases"]:
        print(f"[!] 지도 미표시 국가 {len(st['unmapped_countries'])}개, 미매칭 감염병 {len(st['unmatched_diseases'])}개 "
              f"→ {_show(report)} 확인")
    return 0


if __name__ == "__main__":
    sys.exit(main())
