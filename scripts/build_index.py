"""색인 스크립트 (F2001, F2002, F2005).

python -m scripts.build_index                 # 위키 제목 해석 → 항목 문서 수집(캐시) → 메타데이터 색인
python -m scripts.build_index --no-wiki       # 네트워크 없이: CSV + 이미 받아 둔 위키 본문만 사용
python -m scripts.build_index --prefetch-docs # 133개 항목 자체 문서도 disease_docs에 미리 색인 (첫 질문 속도↑)
python -m scripts.build_index --rechunk       # 저장된 위키 본문을 현재 청크 설정으로 다시 색인
python -m scripts.build_index --chunk-size 500 --chunk-overlap 100 --rechunk   # 설정 비교 실험
python -m scripts.build_index --no-public-docs # 공공데이터 문서(검역관리지역·감염병포털 통계) 색인 생략

공공데이터 문서(F2016): data/processed/country_watch.json·age_stats.json이 있으면 감염병별 레코드 문서로 만들어
disease_docs에 위키 문서와 함께 색인한다 (data_prep/public_docs.py). 파일을 새로 만들었으면 이 스크립트를 다시 실행.

몇 번을 실행해도 같은 항목·청크가 중복 저장되지 않는다 (청크 ID 고정 + upsert).
위키 호출은 중간에 끊겨도 다시 실행하면 이어서 진행한다 (제목·본문 모두 파일 캐시).
"""
from __future__ import annotations

import argparse
import sys
from collections import Counter

from data_prep import settings as paths
from data_prep.collector import WikiCollector
from data_prep.diseases import build_index_text, build_metadata, load_diseases
from data_prep.wiki_titles import load_overrides, load_titles, resolve_titles, save_titles


def _progress(label: str):
    def show(i: int, n: int, name: str) -> None:
        if i == n or i % 10 == 0:
            print(f"  {label} {i}/{n} ({name})", flush=True)
    return show


def main(argv=None, embeddings=None, wiki_client=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-wiki", action="store_true")
    ap.add_argument("--refresh-titles", action="store_true", help="위키 제목을 처음부터 다시 해석")
    ap.add_argument("--prefetch-docs", action="store_true")
    ap.add_argument("--rechunk", action="store_true")
    ap.add_argument("--chunk-size", type=int)
    ap.add_argument("--chunk-overlap", type=int)
    ap.add_argument("--no-public-docs", action="store_true", help="공공데이터 문서 색인 생략")
    args = ap.parse_args(argv)

    paths.ensure_dirs()
    settings = paths.load_rag_settings(chunk_size=args.chunk_size, chunk_overlap=args.chunk_overlap)
    if embeddings is None:
        paths.env("OPENAI_API_KEY", required=True)
        from vectorstore.store import make_embeddings
        embeddings = make_embeddings(settings)
    from vectorstore.store import DiseaseStore
    store = DiseaseStore(settings, embeddings)

    diseases = load_diseases(paths.DISEASES_CSV)
    print(f"[load] 감염병 {len(diseases)}건 로드: " +
          ", ".join(f"{g}급 {c}건" for g, c in sorted(Counter(d.grade for d in diseases).items())))

    titles = {} if args.refresh_titles else load_titles(paths.WIKI_TITLES)
    if not args.no_wiki and wiki_client is None:
        from service.runtime import make_wiki_client
        wiki_client = make_wiki_client(settings)
    collector = WikiCollector(wiki_client, paths.WIKI_PAGES, paths.QUERY_CACHE) if wiki_client else None

    if wiki_client is not None:
        print("[wiki] 항목별 위키백과 문서 제목 해석 (page.exists → 없으면 search)")
        titles = resolve_titles(diseases, wiki_client, load_overrides(paths.CONFIG / "wiki_title_overrides.json"),
                                done=titles, progress=_progress("제목"))
        save_titles(paths.WIKI_TITLES, titles)
        print(f"[wiki] 제목 해석 결과: {dict(Counter(t['method'] for t in titles.values()))} "
              f"→ search/not_found 항목은 {paths.WIKI_TITLES.name}에서 확인")
        print("[wiki] 항목 문서 본문 수집 (page.text, 이미 받은 문서는 건너뜀)")
        targets = [(d, titles[d.disease_id]) for d in diseases if titles.get(d.disease_id, {}).get("pageid")]
        show = _progress("본문")
        for i, (d, t) in enumerate(targets, start=1):
            collector.get_page(int(t["pageid"]), t["title"])
            show(i, len(targets), d.display_name)

    reader = collector or WikiCollector(None, paths.WIKI_PAGES, paths.QUERY_CACHE)
    texts, metas, with_summary = [], [], 0
    for d in diseases:
        t = titles.get(d.disease_id, {})
        pid = int(t.get("pageid") or 0)
        summary = reader.get_page(pid, t["title"])["summary"] if pid and reader.has_page(pid) else ""
        with_summary += bool(summary)
        texts.append(build_index_text(d, summary[:400] if summary else None))
        metas.append(build_metadata(d, t))
    before = store.meta_count()
    try:
        after = store.index_meta(texts, metas)
    except Exception as exc:  # noqa: BLE001 - 임베딩 API 오류를 알아보기 쉽게 안내
        if type(exc).__module__.startswith("openai"):
            print(f"[x] 임베딩 API 호출 실패: {type(exc).__name__}. 위키 수집 결과는 저장되어 있으니 "
                  "원인을 고친 뒤 다시 실행하면 임베딩부터 이어서 합니다.\n"
                  "    먼저 'python -m scripts.check_openai'로 연결을 점검하세요.")
            return 1
        raise
    print(f"[index] {settings['meta_collection']}: {before}건 → {after}건 (새로 임베딩 {store.last_meta_written}건) "
          f"(로드 {len(diseases)}건과 일치: {after == len(diseases)}, 위키 요약 포함 {with_summary}건)")

    if args.prefetch_docs or args.rechunk:
        total = 0
        if args.prefetch_docs:  # 항목 자체 문서를 미리 색인
            for d in diseases:
                t = titles.get(d.disease_id, {})
                pid = int(t.get("pageid") or 0)
                if pid and reader.has_page(pid) and not store.is_indexed(d.disease_id, f"wiki-{pid}"):
                    total += store.index_page(d.disease_id, d.display_name, reader.get_page(pid, t["title"]),
                                              query=d.display_name)
        if args.rechunk:  # 이미 색인된 (감염병, 문서) 쌍을 저장된 본문으로 현재 설정에 맞춰 다시 자른다
            got = store.docs._collection.get(where={"source_type": "wiki"}, include=["metadatas"])
            pairs = {(m["disease_id"], m["disease_name"], m["doc_id"], m["title"], m.get("query", ""))
                     for m in got["metadatas"]}
            for did, name, doc_id, title, query in pairs:
                pid = int(doc_id.split("-")[1])
                if reader.has_page(pid):
                    total += store.index_page(did, name, reader.get_page(pid, title), query=query)
        print(f"[index] {settings['docs_collection']}: 이번에 쓴 청크 {total}개, 전체 {store.docs_count()}개 "
              f"(chunk_size={settings['chunk_size']}, overlap={settings['chunk_overlap']})")
    if not args.no_public_docs:
        from data_prep.public_docs import index_public_docs
        from service.age import load_age_stats
        from service.countries import load_watch
        watch = load_watch(paths.PROCESSED / "country_watch.json")
        stats = load_age_stats(paths.PROCESSED / "age_stats.json")
        if watch.loaded or stats.loaded:
            try:
                r = index_public_docs(store, diseases, watch, stats)
            except Exception as exc:  # noqa: BLE001
                if type(exc).__module__.startswith("openai"):
                    print(f"[x] 공공데이터 문서 임베딩 실패: {type(exc).__name__}. 다시 실행하면 이미 넣은 문서는 건너뛰고 "
                          "이어서 합니다 (python -m scripts.check_openai로 연결 점검).")
                    return 1
                raise
            print(f"[index] 공공데이터 문서 {r['docs']}건 → 청크 {r['chunks']}개 (레코드 = 청크, 종류별 문서 {r['by_type']}, "
                  f"새로 임베딩 {r['written']}건 · 내용 같아 건너뜀 {r['skipped']}건"
                  + (f", 지정 해제로 지운 문서 {r['removed']}건" if r["removed"] else "") + ")")
        else:
            print("[index] 공공데이터 문서: country_watch.json·age_stats.json이 없어 건너뜀 "
                  "(scripts.fetch_quarantine, scripts.build_age_stats 실행 후 다시)")
    print(f"[index] {settings['docs_collection']} 출처 종류별 청크: {store.count_by_type() or '없음'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
