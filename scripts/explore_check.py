"""조건 기반 탐색 검증 (F2011, 체크리스트 11): 조건을 넣고 결과 항목을 하나씩 데이터와 대조한다.

python -m scripts.explore_check
python -m scripts.explore_check "병원체보유자도 신고해야 하는 제3급 감염병"

조건 문장에서 기계로 확인할 수 있는 부분(급수, 신고 범위)을 뽑아 결과마다 감염병포털 목록 값과 비교한다.
'모기가 옮기는'처럼 데이터에 없는 조건은 LLM이 적은 이유를 함께 출력해 사람이 확인한다.
"""
from __future__ import annotations

import argparse
import sys

DEFAULT_CONDITIONS = [
    "병원체보유자도 신고해야 하는 제3급 감염병",
    "의사환자는 신고하지 않는 제2급 감염병",
    "제1급 감염병 중 호흡기로 전파되는 것",
    "모기가 옮기는 제3급 감염병",
]
def constraints(cond: str) -> dict:
    """서비스와 같은 규칙(service/explore.structured_conditions)으로 데이터로 확인할 조건을 뽑는다."""
    from service.explore import structured_conditions
    c = structured_conditions(cond)
    return {**({"grade": {str(g) for g in c["grade"]}} if c.get("grade") else {}), **({"scope": c["scope"]} if c.get("scope") else {})}


def check(results: list[dict], cons: dict, by_id: dict) -> list[dict]:
    rows = []
    for r in results:
        d = by_id.get(r["disease_id"])
        bad = []
        if d is None:
            bad.append("목록에 없는 ID")
        else:
            if "grade" in cons and d.grade not in cons["grade"]:
                bad.append(f"급수 제{d.grade}급")
            for who, want in cons.get("scope", {}).items():
                if d.scope.get(who) != want:
                    bad.append(f"{who} {'포함' if d.scope.get(who) else '제외'}")
        rows.append({**r, "ok": not bad, "bad": bad, "scope": d.scope if d else {}})
    return rows


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("conditions", nargs="*")
    args = ap.parse_args(argv)
    from service.explore import find_diseases
    from service.runtime import build_runtime, setup_logging
    from data_prep import settings as paths

    setup_logging()
    settings = paths.load_rag_settings()
    settings.update(warmup=False, answer_cache=False)
    rt = build_runtime(settings, collect=False)
    total_bad = total = 0
    for cond in args.conditions or DEFAULT_CONDITIONS:
        cons = constraints(cond)
        results = find_diseases(cond, rt.store, rt.services.llm, rt.settings["condition_candidates"])
        rows = check(results, cons, rt.by_id)
        shown = {k: (sorted(v) if isinstance(v, set) else v) for k, v in cons.items()}
        print(f"\n== 조건: {cond}\n   데이터로 확인할 조건: {shown or '없음 (이유를 사람이 확인)'} · 결과 {len(rows)}건")
        for r in rows:
            scope = ", ".join(f"{k} {'O' if v else 'X'}" for k, v in r["scope"].items())
            mark = "맞음" if r["ok"] else "어긋남: " + ", ".join(r["bad"])
            print(f"   - [{mark}] {r['name']} (제{r['grade']}급 · {scope})\n       이유: {r['reason']}")
        total += len(rows)
        total_bad += sum(not r["ok"] for r in rows)
    print(f"\n[결과] 조건과 어긋난 항목 {total_bad}건 / 전체 {total}건")
    return 0 if total_bad == 0 and total > 0 else 1


if __name__ == "__main__":
    sys.exit(main())
