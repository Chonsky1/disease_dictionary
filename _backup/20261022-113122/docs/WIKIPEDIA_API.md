# 위키백과 API 사용법 (Wikipedia-API 라이브러리)

이 프로젝트는 위키백과에 [Wikipedia-API](https://pypi.org/project/Wikipedia-API/) 라이브러리(`import wikipediaapi`, 0.16.0)로 접근한다.
라이브러리가 MediaWiki Action API(`https://ko.wikipedia.org/w/api.php`)를 대신 호출하므로 URL·파라미터를 직접 다루지 않는다.
API 키는 필요 없고, **연락처가 들어간 User-Agent**만 있으면 된다.

```bash
pip install Wikipedia-API==0.16.0      # requirements.txt에 포함
python -m scripts.wiki_demo 홍역        # 아래 기능을 실제로 호출해 보는 예제
```

## 1. 기본 사용법

```python
import wikipediaapi

wiki = wikipediaapi.Wikipedia('kdca-rag/0.1 (contact: me@example.com)', 'ko')
page = wiki.page('홍역')        # 객체만 만든다. 이 시점에는 네트워크 호출이 없다 (lazy)

page.exists()      # 문서 존재 여부. 리다이렉트도 따라간다 ("Measles" → "홍역")
page.title         # 리다이렉트를 해석한 실제 제목
page.pageid        # 문서 번호 → 청크 ID의 문서 ID로 사용 (wiki-<pageid>)
page.fullurl       # 원문 출처 URL → 근거 표시에 사용
page.summary       # 도입부 → 메타데이터 색인 텍스트의 '개요'
page.text          # 본문 전체 (섹션 제목 포함 평문)
page.sections      # 섹션 트리: s.title, s.level, s.text, s.sections(하위)

res = wiki.search('홍역 바이러스', limit=5)   # 전문 검색
for title, p in res.pages.items():
    print(title, p.pageid, p.search_meta.snippet)   # 스니펫 = 프롬프트 2에 보여 줄 요약
```

## 2. 속성마다 실제로 나가는 요청

라이브러리는 속성에 처음 접근할 때 필요한 요청을 보내고, 결과를 page 객체에 저장해 둔다.
가짜 서버로 요청을 기록해 확인한 결과다 (`tests/test_stage1.py::WikiClientTest`).

| 코드 | 실제 요청 | 함께 채워지는 값 |
| --- | --- | --- |
| `page.exists()` | `action=query&prop=info&redirects=1&titles=…` | `title`, `pageid`, `fullurl`, `lastrevid`, `length` |
| `page.text` / `page.summary` / `page.sections` | `prop=extracts&explaintext=1&exsectionformat=wiki` | 셋 다 한 번의 응답으로 채워짐 |
| `wiki.search(q, limit=5)` | `list=search&srsearch=q&srlimit=5&srnamespace=0` | 결과 page마다 `pageid`, `search_meta.snippet` |

정리하면 **문서 하나를 받는 데 2회**(exists + text), **검색 한 번에 1회**가 든다.

## 3. 프로젝트에서 쓰는 방식 (`data_prep/wiki.py`의 `WikiClient`)

`WikiClient`는 `wikipediaapi.Wikipedia`를 감싸 아래를 더한다.

| 추가한 것 | 이유 |
| --- | --- |
| User-Agent에 연락처가 없으면 시작 시 오류 | 연락처 없는 요청은 차단될 수 있다 (NF2006) |
| 요청 사이 최소 간격 0.5초 (`config/rag_settings.json`의 `wiki_min_interval_sec`) | 라이브러리에는 호출 간격 기능이 없다. 한 번에 하나씩 천천히 호출한다 |
| `max_retries=3`, `retry_wait=1.0` | 429·5xx 응답이면 라이브러리가 재시도하고 `Retry-After`를 따른다 |
| `extra_api_params={"maxlag": 5}` | 위키 서버가 바쁠 때 요청을 미루라는 Wikimedia 권장 파라미터 |
| 제목에 `(동음이의)`가 들어간 문서 제외 | 동음이의 문서는 근거로 쓸 수 없다 |
| 섹션 트리를 `[("개요", …), ("증상", …), ("증상 > 합병증", …)]`로 펼침, 각주·외부 링크 제외 | 섹션 이름을 청크 머리말로 붙이기 위해 |

| 메서드 | 쓰는 곳 |
| --- | --- |
| `resolve(title)` | 감염병별 항목 문서 제목 확정 (`scripts/build_index.py`) |
| `search_candidates(query)` | 검색어별 후보 → 프롬프트 2 선별 (`data_prep/collector.py`) |
| `fetch_page(title)` | 선별된 문서 본문 수집 → `data/raw/wiki/pages/<pageid>.json` 저장 |

## 4. 호출 수를 줄이는 캐시 (NF2002)

| 캐시 | 파일 | 효과 |
| --- | --- | --- |
| 항목 문서 제목 | `data/processed/wiki_titles.json` | 색인을 다시 돌려도 제목 확인 요청 0회 |
| 문서 본문 | `data/raw/wiki/pages/<pageid>.json` | 같은 문서는 한 번만 받는다 |
| 검색어 | `data/cache/query_cache.json` | 이미 수집한 검색어는 검색·선별·수집을 모두 건너뛴다 |

첫 색인은 133개 감염병마다 제목 확인과 본문 수집이 필요해 약 300회 호출(간격 0.5초 기준 2~3분)이 든다.
중간에 끊겨도 다시 실행하면 이어서 진행한다.

## 5. 라이선스

위키백과 본문은 CC BY-SA 4.0이다. 근거를 표시할 때 문서 제목과 원문 링크(`fullurl`)를 함께 보여 주고,
수집한 본문 파일에 `license` 필드를 남긴다 (NF2007).

## 6. 자주 겪는 문제

| 증상 | 원인 | 해결 |
| --- | --- | --- |
| `WIKI_USER_AGENT에 연락처…` 오류 | `.env`의 User-Agent에 이메일이 없음 | `kdca-rag/0.1 (contact: me@example.com)` 형식 |
| `WikiHttpError (403, …)` | User-Agent 정책 위반 또는 차단 | 연락처 확인, 호출 간격 늘리기 |
| `WikiHttpError (429/503, …)` | 요청이 많거나 서버가 바쁨 | 자동 재시도 후에도 나면 잠시 뒤 다시 실행 (캐시 덕에 이어서 진행) |
| `page.exists()`가 False인데 문서가 있음 | 띄어쓰기·괄호 표기 차이 | 제목 후보가 자동으로 여러 개 시도됨. 그래도 안 되면 `config/wiki_title_overrides.json` |
| 엉뚱한 문서(영화·인물)가 잡힘 | 검색 결과 상위가 다른 뜻 | 프롬프트 2가 걸러 냄. 항목 문서라면 overrides에 정답 제목 지정 |
| 검색 스니펫이 짧음 | `list=search` 스니펫은 일부 문장만 보여 줌 | 선별은 제목+스니펫으로 충분. 본문은 선별 후 `fetch_page`로 받음 |

## 참고

- Wikipedia-API: https://pypi.org/project/Wikipedia-API/
- MediaWiki Action API: https://www.mediawiki.org/wiki/API:Main_page
- Wikimedia 개발자 API 안내: https://developer.wikimedia.org/ko/build-tools/apis/
