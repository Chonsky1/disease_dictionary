"""화면 없이 질문 하나를 끝까지 돌려 중간 결과를 모두 출력한다 (4.2·4.3 검증용).

python -m scripts.ask --disease 홍역 "홍역은 몇 급 감염병이고 언제까지 신고해야 하나요?"
python -m scripts.ask --disease 홍역 --top-k 8 --search-type mmr "…"   # 설정 비교
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
    args = ap.parse_args(argv)

    from rag.graph import run
    from service.runtime import build_runtime, setup_logging

    setup_logging()
    settings = paths.load_rag_settings(top_k=args.top_k, search_type=args.search_type)
    rt = build_runtime(settings, collect=not args.no_collect)
    matches = [d for d in rt.diseases if args.disease in (d.disease_id, d.display_name)] or \
              [d for d in rt.diseases if args.disease in d.name]
    if not matches:
        print(f"'{args.disease}'에 해당하는 감염병이 없습니다.")
        return 1
    d = matches[0]
    print(f"== {d.label()} | top_k={settings['top_k']} search={settings['search_type']}")
    state = run(rt.graph, args.question, d.disease_id, on_event=lambda e: print(f"  … {e['message']}"))

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
    return 0


if __name__ == "__main__":
    sys.exit(main())
