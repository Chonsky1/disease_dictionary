"""도메인 특화 기능 '여행 전 안내' 검증 (F2010, 체크리스트 12).

python -m scripts.travel_check              # 검역감염병이 가장 많은 나라
python -m scripts.travel_check --country VNM

화면(홈 → 나라 팝업 → '여행 전 안내 만들기')과 같은 함수(service/travel.py)를 부르고,
답의 [근거 n]과 근거 목록(출처 종류 포함)을 함께 출력한다.
"""
from __future__ import annotations

import argparse
import sys

from data_prep import settings as paths


def pick_country(watch, by_id, key: str | None):
    if key:
        return watch.countries[key]
    return max(watch.countries.values(), key=lambda c: (sum(1 for x in c.diseases if x.get("disease_id") in by_id),
                                                         c.name_ko))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--country", help="ISO-3 코드 (예: VNM)")
    args = ap.parse_args(argv)
    from service.countries import load_watch
    from service.runtime import build_runtime, setup_logging
    from service.travel import travel_brief

    setup_logging()
    watch = load_watch(paths.PROCESSED / "country_watch.json")
    if not watch.loaded:
        print("country_watch.json이 없습니다. python -m scripts.fetch_quarantine 후 python -m scripts.build_index")
        return 1
    settings = paths.load_rag_settings()
    settings.update(warmup=False, answer_cache=False)
    rt = build_runtime(settings, collect=False)
    c = pick_country(watch, rt.by_id, args.country)
    ids = [x["disease_id"] for x in c.diseases if x.get("disease_id") in rt.by_id]
    print(f"== 나라: {c.name_ko} ({c.key}) · 검역관리지역 {watch.as_of} 기준 · 검역감염병 {len(ids)}종: "
          + ", ".join(rt.by_id[i].display_name for i in ids))
    res = travel_brief(c.name_ko, watch.as_of, ids, rt)
    print("\n[근거 목록] (출처 종류)")
    for e in res["evidence"]:
        print(f"  [근거 {e['n']}] ({e['source_type']}) {e['source_label']} · {e['title']}")
    print("\n[여행 전 안내]\n" + res["answer"])
    kinds = sorted({e["source_type"] for e in res["cited"]})
    print(f"\n[인용된 근거] {[e['n'] for e in res['cited']]} · 출처 종류 {kinds}")
    ok = bool(res["cited"]) and "public_api" in kinds
    print("[결과]", "통과: 특화 기능의 답에 근거가 함께 제시됨" if ok else "확인 필요: 인용 근거 또는 공공데이터 근거 없음")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
