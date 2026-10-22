"""검역관리지역 정보 수집 → data/processed/country_watch.json (지도 나라 패널이 읽는 파일).

python -m scripts.fetch_quarantine                      # Open API (기본: …/QuarantineRegion/NationDisease)
python -m scripts.fetch_quarantine --endpoint https://apis.data.go.kr/…   # 요청주소를 바꿔야 할 때만
python -m scripts.fetch_quarantine --csv 파일.csv        # 파일데이터 CSV로 대신 (API 승인 전·장애 시)
python -m scripts.fetch_quarantine --raw                # API 호출 없이 가장 최근 저장 원본으로 다시 만들기
python -m scripts.fetch_quarantine --raw data/raw/api/quarantine_area/20261022.json   # 특정 날짜 원본
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

# 활용신청 상세기능 기준 (2026-10 확인)
SERVICE_URL = "https://apis.data.go.kr/1790387/QuarantineRegion"
OP_NATION_DISEASE = "NationDisease"   # ② 검역관리지역 조회: 나라별 검역감염병·지정일·해지일 ← 지도에 사용
OP_DISEASE_NATION = "DiseaseNation"   # ① 검역관리지역 설정 조회: 감염병·대상자 그룹별 국가·공항·항구 (확장용)
DEFAULT_ENDPOINT = f"{SERVICE_URL}/{OP_NATION_DISEASE}"


def resolve_endpoint(ep: str | None) -> str:
    """설정·인자의 요청주소를 정리. 비었거나 TODO거나 서비스 기본 주소만 있으면 /NationDisease를 쓴다."""
    ep = (ep or "").strip().rstrip("/")
    if not ep or ep.startswith("TODO"):
        return DEFAULT_ENDPOINT
    if ep.endswith("/QuarantineRegion"):
        return f"{ep}/{OP_NATION_DISEASE}"
    if ep.endswith("/" + OP_DISEASE_NATION):
        raise SystemExit("[x] /DiseaseNation(검역관리지역 설정 조회)은 감염병별 국가 목록을 주는 오퍼레이션이라 지도에 맞지 않습니다.\n"
                         f"    지도에는 {DEFAULT_ENDPOINT} 를 쓰세요 (--endpoint 생략 시 기본값).")
    return ep


def _show(p: Path) -> str:
    try:
        return str(p.relative_to(paths.ROOT))
    except ValueError:
        return str(p)


def _from_api(max_pages: int, endpoint: str | None) -> tuple[list, str]:
    import requests
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
    api["endpoint"] = resolve_endpoint(api.get("endpoint"))
    print(f"[api] 요청주소 {api['endpoint']}")
    key = paths.env("DATA_GO_KR_SERVICE_KEY", required=True).strip().strip('"').strip("'")
    if "%" in key:
        print("[!] 인증키에 '%'가 있습니다. Encoding 키로 보입니다. 마이페이지의 'Decoding' 키로 바꿔 보세요.")
    try:
        items = fetch_all(src, PoliteSession("kdca-rag/0.1", min_interval=0.3), key, max_pages=max_pages)
    except ApiError as exc:
        hint = ("    12(NO_OPENAPI_SERVICE_ERROR)·404면 요청주소가 …/QuarantineRegion/NationDisease 인지 확인하세요.\n"
                "    102·30번대(인증·미등록 키)면 Decoding 키 사용 여부와 활용신청 승인 상태를 확인하세요.\n"
                "    22번(호출 한도 초과)이면 내일 다시 시도하거나 --csv 파일데이터를 쓰세요.")
        raise SystemExit(f"[x] API 오류: {exc}\n{hint}")
    except requests.HTTPError as exc:
        code = exc.response.status_code if exc.response is not None else None
        if code in (401, 403):
            hint = ("    data.go.kr 게이트웨이가 이 키로 이 서비스를 쓰는 것을 막았습니다 (네트워크 문제 아님). 확인 순서:\n"
                    "    1) 마이페이지 > 활용신청 현황에 '질병관리청_검역관리지역정보'가 '승인'인가 (다른 API 키 승인과 별개)\n"
                    "    2) 승인 직후라면 키가 게이트웨이에 반영되기까지 1~2시간 기다린 뒤 재시도\n"
                    "    3) .env의 DATA_GO_KR_SERVICE_KEY가 'Decoding' 키인가 (Encoding 키는 %2B·%3D가 이중 인코딩됨),\n"
                    "       앞뒤 공백·따옴표가 없는가\n"
                    "    4) 그래도 안 되면 마이페이지의 '미리보기/요청 테스트'가 되는지 확인. 거기서도 403이면 포털 문의\n"
                    "    당장은 파일데이터 CSV(https://www.data.go.kr/data/3074726/fileData.do)를 받아 --csv 로 실행하세요.")
        elif code == 404:
            hint = "    요청주소가 …/QuarantineRegion/NationDisease 인지 확인하세요."
        else:
            hint = "    잠시 뒤 다시 시도하거나 --csv 로 파일데이터를 쓰세요."
        raise SystemExit(f"[x] HTTP 오류: {exc}\n{hint}")
    except requests.RequestException as exc:
        raise SystemExit(f"[x] 연결 실패: {exc}\n"
                         "    인터넷 연결·사내망 프록시를 확인하거나, 파일데이터를 받아 --csv 로 실행하세요.")
    raw = save_raw("quarantine_area", items, paths.RAW_API, {"endpoint": src["api"]["endpoint"],
                                                            "params": src["api"].get("params", {})})
    print(f"[api] {len(items)}건 수집 → {_show(raw)}")
    return from_api_items(items), "Open API"


RAW_DIR_NAME = "quarantine_area"


def latest_raw() -> Path:
    files = sorted((paths.RAW_API / RAW_DIR_NAME).glob("*.json"))
    if not files:
        raise SystemExit(f"[x] 저장된 API 원본이 없습니다 ({_show(paths.RAW_API / RAW_DIR_NAME)}).\n"
                         "    먼저 python -m scripts.fetch_quarantine 로 한 번 수집하세요.")
    return files[-1]   # 파일명이 YYYYMMDD.json이라 이름순 마지막이 최신


def _from_raw(path: Path | None) -> tuple[list, str]:
    """API를 다시 부르지 않고 저장된 원본(data/raw/api/quarantine_area/*.json)으로 다시 만든다."""
    p = path or latest_raw()
    if not p.exists():
        raise SystemExit(f"[x] 파일이 없습니다: {p}")
    data = json.loads(p.read_text(encoding="utf-8"))
    items = data.get("items") if isinstance(data, dict) else data
    if not isinstance(items, list):
        raise SystemExit(f"[x] {p.name}에 items 목록이 없습니다. fetch_quarantine가 저장한 원본 파일인지 확인하세요.")
    fetched = str(data.get("fetched_at", ""))[:10] if isinstance(data, dict) else ""
    print(f"[raw] {_show(p)}: {len(items)}건" + (f" (수집일 {fetched})" if fetched else ""))
    return from_api_items(items), f"Open API 저장본 {fetched or p.stem}"


def _from_csv(path: Path) -> tuple[list, str]:
    from data_prep.csv_loader import read_table
    header, rows, meta = read_table(path)
    print(f"[csv] {path.name}: {len(rows)}행 (인코딩 {meta['encoding']})")
    return from_csv_rows(header, rows), f"파일데이터 {path.name}"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    src = ap.add_mutually_exclusive_group()
    src.add_argument("--csv", type=Path, help="파일데이터 CSV로 만들기")
    src.add_argument("--raw", nargs="?", const="latest", metavar="JSON",
                     help="API를 부르지 않고 저장된 원본으로 다시 만들기 (경로 생략 시 가장 최근 것)")
    ap.add_argument("--endpoint", help="API 요청주소 (config/sources.json 대신)")
    ap.add_argument("--date", help="기준일 YYYY-MM-DD (이 날짜에 지정 중인 것만)")
    ap.add_argument("--max-pages", type=int, default=50)
    args = ap.parse_args(argv)
    paths.ensure_dirs()

    if args.csv:
        records, label = _from_csv(args.csv)
    elif args.raw:
        records, label = _from_raw(None if args.raw == "latest" else Path(args.raw))
    else:
        records, label = _from_api(args.max_pages, args.endpoint)
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
