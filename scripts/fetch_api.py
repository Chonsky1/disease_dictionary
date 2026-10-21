"""공공데이터포털 API 원본 수집 + 응답 필드 목록 작성.

실행: python -m scripts.fetch_api --source quarantine_area
결과: data/raw/api/<source>/<YYYYMMDD>.json, reports/api_fields_<source>.md
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter

from data_prep import settings
from data_prep.http_client import PoliteSession
from data_prep.kdca_api import ApiError, fetch_all, save_raw


def load_source(source_id: str) -> dict:
    cfg = json.loads((settings.CONFIG / "sources.json").read_text(encoding="utf-8"))
    for s in cfg["sources"]:
        if s["id"] == source_id:
            return s
    raise SystemExit(f"config/sources.json에 '{source_id}' 출처가 없습니다.")


def field_inventory(items: list[dict]) -> str:
    """API 응답에 어떤 필드가 얼마나 채워져 있는지 (EDA 첫 단계)."""
    n = len(items)
    filled, examples = Counter(), {}
    for it in items:
        for k, v in it.items():
            if v not in (None, "", " "):
                filled[k] += 1
                examples.setdefault(k, str(v)[:40])
    keys = sorted({k for it in items for k in it})
    rows = [f"| {k} | {filled[k]}/{n} | {examples.get(k, '')} |" for k in keys]
    return "\n".join(["| 필드 | 채움 | 예시 |", "| --- | --- | --- |", *rows])


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", required=True)
    ap.add_argument("--max-pages", type=int, default=200)
    args = ap.parse_args(argv)

    settings.ensure_dirs()
    src = load_source(args.source)
    if src.get("kind") != "api":
        raise SystemExit(f"'{args.source}'는 API 출처가 아닙니다 (kind={src.get('kind')}).")
    if str(src["api"].get("endpoint", "")).startswith("TODO"):
        raise SystemExit("endpoint가 TODO입니다. 활용신청 상세 화면의 요청주소를 config/sources.json에 적어 주세요.")

    key = settings.env("DATA_GO_KR_SERVICE_KEY", required=True)
    session = PoliteSession(user_agent="kdca-rag/0.1", min_interval=0.5)
    try:
        items = fetch_all(src, session, key, max_pages=args.max_pages)
    except ApiError as exc:
        print(f"[api] {args.source} 실패: {exc}", file=sys.stderr)
        print("  - 인증 오류면: Decoding 키 사용 여부, 활용신청 승인(수 시간 걸릴 수 있음) 확인", file=sys.stderr)
        return 1

    path = save_raw(args.source, items, settings.RAW_API,
                    {"endpoint": src["api"]["endpoint"], "params": src["api"].get("params", {})})
    report = settings.REPORTS / f"api_fields_{args.source}.md"
    report.write_text(f"# {src['name']} 응답 필드\n\n- 항목 수: {len(items)}\n\n{field_inventory(items)}\n",
                      encoding="utf-8")
    print(f"[api] {args.source}: {len(items)}건 → {path.relative_to(settings.ROOT)}, 필드 목록 → {report.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
