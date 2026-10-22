"""화면 없이 질문 하나를 끝까지 돌려 중간 결과를 모두 출력한다 (4.2·4.3 검증용).

python -m scripts.ask --disease 홍역 "홍역은 몇 급 감염병이고 언제까지 신고해야 하나요?"
python -m scripts.ask --disease 홍역 --top-k 8 --search-type mmr "…"   # 설정 비교
python -m scripts.ask --disease 홍역 --policy background "…"          # 앱 기본값(배경 수집)으로

검증(4.2)용으로 기본은 --policy always: 질문마다 프롬프트 1 검색어와 프롬프트 2 선별 결과(색인/버림)를
그 자리에서 보여 준다. 같은 질문 저장 답변(answer_cache)은 검증을 위해 끈다 (--use-cache로 켬).
"""
from __future__ import annotations

import argparse
import sys

from data_prep import settings as paths


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("question")
    ap.add_argument("--disease", required=True, help="감염병 이름 일부 또는 inf_0001 형식 ID")
    ap.add_argument("--top-k", type=int)
    ap.add_argument("--search-type", choices=["similarity", "mmr"])
    ap.add_argument("--no-collect", action="store_true", help="외부 수집 없이 색인된 문서만 사용")
    ap.add_argument("--policy", choices=["always", "when_needed", "background"], default="always",
                    help="수집 정책 (기본 always = 검증용, 앱 기본값은 background)")
    ap.add_argument("--use-cache", action="store_true", help="같은 질문 저장 답변 사용")
    args = ap.parse_args(argv)

    from rag.graph import run
    from service.runtime import build_runtime, setup_logging

    setup_logging()
    settings = paths.load_rag_settings(top_k=args.top_k, search_type=args.search_type)
    settings.update(collect_policy=args.policy, answer_cache=args.use_cache, warmup=False)
    rt = build_runtime(settings, collect=not args.no_collect)
    matches = [d for d in rt.diseases if args.disease in (d.disease_id, d.display_name)] or \
              [d for d in rt.diseases if args.disease in d.name]
    if not matches:
        print(f"'{args.disease}'에 해당하는 감염병이 없습니다.")
        return 1
    d = matches[0]
    print(f"== {d.label()} | top_k={settings['top_k']} search={settings['search_type']} policy={args.policy}")

    def on_event(e: dict) -> None:
        if e.get("message"):            # 답변 글자(stage=token)는 [답변]에서 한 번에 출력
            print(f"  … {e['message']}")
    state = run(rt.graph, args.question, d.disease_id, on_event=on_event)

    print("\n[프롬프트 1] 검색어:", state.get("queries"))
    for row in state.get("collect_log", []):
        tag = "캐시" if row["from_cache"] else "수집"
        print(f"[수집] ({row['kind']}/{tag}) {row['query']} → 색인 {row['indexed'] or '-'} "
              f"/ 버림 {row['rejected'] or '-'} / 청크 {row['chunks']}")
    print("\n[근거 목록]")
    for e in state["evidence"]:
        print(f"  [근거 {e['n']}] {e['source_label']} · {e['title']} {('/ ' + e['section']) if e['section'] else ''} · {e['url']}")
    print("\n[답변]\n" + state["answer"])
    cited = state.get("cited", [])
    print("\n[인용된 근거]", [f"근거 {e['n']}" for e in cited] or "없음 (화면에 안내 표시)")
    from rag.metrics import describe
    print("\n[시간]", describe(state.get("timings", {})))
    if args.policy == "background":   # 배경 수집이 끝날 때까지 기다렸다가 결과를 보여 준다
        import time
        from rag.graph import describe_background
        while d.disease_id in rt.services.background:
            time.sleep(0.2)
        report = rt.services.background_log.get(d.disease_id)
        if report:
            print("[배경 수집]", " / ".join(describe_background(report)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
