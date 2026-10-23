"""색인 상태와 그래프 구조 확인 (4.1, 4.3 검증용).

python -m scripts.inspect_index            # 건수, 항목 1건의 색인 텍스트, 그래프 구조
python -m scripts.inspect_index --disease 홍역
"""
from __future__ import annotations

import argparse
import sys

from data_prep import settings as paths
from data_prep.diseases import load_diseases


def main(argv=None, embeddings=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--disease", default="홍역")
    args = ap.parse_args(argv)
    settings = paths.load_rag_settings()

    from langchain_core.embeddings import DeterministicFakeEmbedding

    from vectorstore.store import DiseaseStore
    # 조회만 하므로 임베딩 호출이 필요 없다 (키 없이 실행 가능)
    store = DiseaseStore(settings, embeddings or DeterministicFakeEmbedding(size=8))
    diseases = load_diseases(paths.DISEASES_CSV)
    print(f"로드한 항목 {len(diseases)}건 / {settings['meta_collection']} 청크 {store.meta_count()}건 / "
          f"{settings['docs_collection']} 청크 {store.docs_count()}건")
    print(f"{settings['docs_collection']} 출처 종류별 청크: {store.count_by_type() or '없음'} "
          "(wiki=위키백과, public_api=검역관리지역 API, public_stats=감염병포털 통계)")

    d = next((x for x in diseases if args.disease in x.name), diseases[0])
    meta = store.get_meta(d.disease_id)
    print(f"\n[{d.disease_id}] 색인 텍스트:\n{meta.page_content if meta else '(색인 없음)'}")
    print(f"\n이 감염병으로 색인된 문서: {store.list_docs(d.disease_id) or '없음'}")

    from rag.graph import RagServices, build_graph
    g = build_graph(RagServices(llm=None, store=store, collector=None, diseases={}, wiki_titles={}, settings=settings))
    print("\n[LangGraph 구조]")
    print(g.get_graph().draw_mermaid())
    return 0


if __name__ == "__main__":
    sys.exit(main())
