# 법정감염병 안내 도우미 (단계 1)

법정감염병 133종에 대한 질문에 **감염병포털 목록 + 한국어 위키백과**를 근거로 답하고,
답변에 쓴 근거의 제목과 원문 출처를 함께 보여 주는 RAG 서비스입니다.

> 이 서비스는 일반 정보이며 의사·약사 등 전문가의 진단이나 조언을 대신하지 않습니다.

## 1. 설치 (Windows Git Bash 기준)

```bash
cd ~/Desktop/dj/kdca-rag
python -m venv venv
source venv/Scripts/activate        # macOS/Linux: source venv/bin/activate
export PYTHONUTF8=1                 # 한글 인코딩 오류 예방 (~/.bashrc에 넣어 두면 편함)
pip install -r requirements.txt
cp .env.example .env                # OPENAI_API_KEY, WIKI_USER_AGENT 입력
python -m unittest discover -s tests -t .   # 키·네트워크 없이 42개 테스트
```

## 2. 색인 → 실행

```bash
python -m scripts.check_openai                 # OpenAI/GMS 연결 점검 (채팅·임베딩 각각)
python -m scripts.wiki_demo 홍역               # 위키백과 연결 확인 (page.exists / summary / sections / search)
python -m scripts.build_index                  # 133건 메타데이터 색인 (위키 제목 해석·본문 수집 포함, 첫 실행 2~3분)
python -m scripts.build_index --prefetch-docs  # (선택) 133개 항목 문서를 미리 색인 → 첫 질문이 빨라짐
streamlit run ui/app.py                        # 반드시 kdca-rag 폴더에서 실행 (.streamlit/config.toml 적용)
```

색인 후 `data/processed/wiki_titles.json`에서 `"method": "search"` 또는 `"not_found"`인 항목을 확인하세요.
잘못 잡힌 문서는 `config/wiki_title_overrides.json`에 `"CSV 감염병명": "위키 문서 제목"`으로 적고
`python -m scripts.build_index --refresh-titles`를 다시 실행합니다.

## 3. 구조 (NF2005)

| 영역 | 파일 | 하는 일 |
| --- | --- | --- |
| 데이터 준비 | `data_prep/diseases.py` | CSV 로드, 항목 1건 = 색인 텍스트 1개 (F2001) |
| | `data_prep/wiki.py`, `wiki_titles.py` | `wikipediaapi` 라이브러리로 위키백과 조회·검색·본문 수집, 항목 문서 제목 해석 ([사용법](docs/WIKIPEDIA_API.md)) |
| | `data_prep/collector.py` | 검색어 캐시, 본문 저장 (F2004, NF2002) |
| | `data_prep/http_client.py` | 호출 간격·재시도·User-Agent·키 마스킹 (NF2004, NF2006) |
| Vector Store | `vectorstore/store.py` | `disease_meta`, `disease_docs` 두 컬렉션, 청킹 (F2002, F2005, F2014) |
| RAG | `rag/prompts.py` | 프롬프트 3종 + 조건 탐색 1종 |
| | `rag/graph.py` | LangGraph `retrieve` → `generate` (F2003, F2006~F2008) |
| 서비스 | `service/explore.py` | 조건 기반 탐색 (F2011) |
| UI | `ui/app.py` | 한 페이지 레이아웃: 상단 내비게이션 → 감염병 질문하기(F2009, F2011, F2012) → 나라별 지도 → 연령별 |
| | `ui/map_view.py`, `ui/styles.py` | 클릭 가능한 세계 지도(plotly), 화면 스타일 |
| | `ui/static/topojson/` | 지도 경계 파일 (CDN이 막힌 망에서도 지도가 보이도록 앱과 함께 제공) |
| 서비스 | `service/countries.py` | 나라 목록, 나라별 주의 감염병 데이터 읽기 (단계 3에서 `data/processed/country_watch.json` 생성) |
| 설정 | `config/rag_settings.json` | 청크 크기·중복, Top-k, 검색 방식, 모델 |

## 4. 컬렉션과 청크 규칙

| 컬렉션 | 단위 | 청크 ID | 주요 메타데이터 |
| --- | --- | --- | --- |
| `disease_meta` | 감염병 1건 = 1청크 | `inf_0021` (재색인해도 덮어쓰기) | grade, group, kcd, scope_*, wiki_title, source_url |
| `disease_docs` | 위키 본문 청크 | `inf_0021:wiki-12345:0003` | disease_id, doc_id, title, section, source_url, query, collected_at |

외부 문서 청크 앞에는 `[감염병: 홍역 | 문서: … | 섹션: 증상]` 머리말을 붙여, 잘린 조각도 어느 감염병 이야기인지 검색에 걸리게 했습니다.

## 5. 검증 명령 (명세서 4장)

| 확인 항목 | 명령 |
| --- | --- |
| 4.1 로드 건수 = 색인 건수, 두 번 실행해도 불변, 색인 텍스트 | `python -m scripts.build_index` 2회 → `python -m scripts.inspect_index --disease 홍역` |
| 4.2 프롬프트 1 검색어, 선별/버린 문서, 재질문 시 캐시 | `python -m scripts.ask --disease 홍역 "홍역은 어떻게 전파되나요?"` 2회 |
| 4.2 청크 설정 변경 시 청크 수 | `python -m scripts.build_index --rechunk --chunk-size 500 --chunk-overlap 100` |
| 4.3 Top-k 변경, MMR 비교 | `python -m scripts.ask --disease 홍역 --top-k 8 --search-type mmr --no-collect "…"` |
| 4.3 2노드 그래프 구조 | `python -m scripts.inspect_index` (Mermaid 출력) |
| 4.3 문서에 없는 질문 | `python -m scripts.ask --disease 홍역 "홍역 치료제 가격은 얼마인가요?"` |
| 4.4 화면 | `streamlit run ui/app.py`, 키를 비운 채 실행, 색인 폴더(`data/chroma`)를 지운 채 실행 |

자동 테스트(`tests/test_stage1.py`, `tests/test_ui.py`)가 위 항목 대부분을 가짜 LLM·가짜 위키로 확인합니다.

## 6. 출처와 이용 조건 (NF2007)

| 데이터 | 출처 | 이용 조건 |
| --- | --- | --- |
| 법정감염병 목록 133건 | [질병관리청 감염병포털](https://dportal.kdca.go.kr/pot/ii/sttyInftnsds/sttyInftnsds.do) | 공공누리 유형 확인 후 기록 |
| 감염병 설명 문서 | [한국어 위키백과](https://ko.wikipedia.org) | CC BY-SA 4.0, 근거마다 제목·원문 링크 표시 |
