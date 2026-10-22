"""wikipediaapi 사용 예시 — 프로젝트에서 쓰는 기능을 하나씩 실제로 호출해 본다.

python -m scripts.wiki_demo                 # 기본: 홍역
python -m scripts.wiki_demo 쯔쯔가무시증
"""
from __future__ import annotations

import sys

import wikipediaapi

from data_prep import settings as paths


def main(argv=None) -> int:
    title = (argv or sys.argv[1:] or ["홍역"])[0]
    ua = paths.env("WIKI_USER_AGENT", required=True)        # 예: kdca-rag/0.1 (contact: me@example.com)

    # 1) 기본 사용법 (질문에서 주신 예시와 동일)
    wiki = wikipediaapi.Wikipedia(ua, "ko")
    page = wiki.page(title)                                   # 아직 네트워크 호출 없음 (lazy)
    print(f"[exists] {page.exists()}  → prop=info 호출 1회, 리다이렉트 자동 해석")
    if not page.exists():
        print("문서가 없습니다. 검색 결과:", [t for t in wiki.search(title, limit=5).pages])
        return 1
    print(f"[title]  {page.title} (pageid {page.pageid})")
    print(f"[url]    {page.fullurl}")
    print(f"[summary] {page.summary[:150]}…  → prop=extracts 호출 1회 (text·sections도 함께 채워짐)")
    print(f"[text]   {len(page.text)}자")

    # 2) 섹션 트리: 청킹할 때 섹션 이름을 머리말로 붙이는 데 쓴다
    def walk(sections, depth=0):
        for s in sections:
            print("  " * depth + f"- {s.title} ({len(s.text)}자)")
            walk(s.sections, depth + 1)
    print("[sections]")
    walk(page.sections)

    # 3) 검색: 프롬프트 2가 볼 후보(제목 + 스니펫)
    res = wiki.search(f"{title} 전파", limit=5)
    print(f"[search] '{title} 전파' 총 {res.totalhits}건 중 상위 {len(res.pages)}건")
    for t, p in res.pages.items():
        print(f"  - {t} (pageid {p.pageid})")

    # 4) 프로젝트 래퍼: 간격·재시도·maxlag 적용 + 저장용 dict
    from data_prep.wiki import WikiClient
    client = WikiClient(ua)
    doc = client.fetch_page(title)
    print(f"[WikiClient.fetch_page] {doc['doc_id']} 섹션 {len(doc['sections'])}개 → "
          f"{[n for n, _ in doc['sections']][:6]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
