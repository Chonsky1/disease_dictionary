# 법정감염병 안내 도우미

SSAFY AI 코어프로젝트 2회차 · 법정감염병 133종 질의응답(RAG) + 나라별 검역감염병 지도 + 연령별·시기별 통계

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
python -m unittest discover -s tests -t .   # 키·네트워크 없이 87개 테스트
```

## 2. 색인 → 실행

```bash
python -m scripts.check_openai                 # OpenAI/GMS 연결 점검 (채팅·임베딩 각각)
python -m scripts.wiki_demo 홍역               # 위키백과 연결 확인 (page.exists / summary / sections / search)
python -m scripts.build_index                  # 133건 메타데이터 색인 (위키 제목 해석·본문 수집 포함, 첫 실행 2~3분)
python -m scripts.build_index --prefetch-docs  # (권장) 133개 항목 문서를 미리 색인 → 질문당 평균 4.6초 (docs/PERFORMANCE.md)
python -m scripts.fetch_quarantine             # 나라별 검역감염병 수집 (…/QuarantineRegion/NationDisease, 또는 --csv 파일데이터)
python -m scripts.fetch_quarantine --raw       # (선택) API 호출 없이 마지막 저장 원본으로 다시 만들기 (변환표 수정 후 등)
python -m scripts.build_age_stats              # 연령별·시기별 통계 (data/raw/stats/의 감염병포털 CSV·xlsx → age_stats.json)
streamlit run ui/app.py                        # 반드시 프로젝트 폴더에서 실행 (.streamlit/config.toml 적용)
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
| RAG | `rag/prompts.py` | 프롬프트 3종 + 조건 탐색 1종. 답변은 '### 핵심 답변 / 초기 증상 / 전체 증상 / 잠복기 / 치사율·중증도 / 전염성·전파 경로 / 발생 지역 / 치료 / 예방 / 신고·관리' 중 해당 항목만 |
| | `rag/metrics.py`, `rag/answer_cache.py` | 답변 시간·호출 수 기록, 같은 질문 저장 답변 |
| | `rag/graph.py` | LangGraph `retrieve` → `generate` (F2003, F2006~F2008). 배경 수집·병렬 수집·스트리밍 ([속도](docs/PERFORMANCE.md)) |
| 서비스 | `service/explore.py` | 조건 기반 탐색 (F2011) |
| UI | `ui/app.py` | 페이지 라우터(다크 테마) + 항상 보이는 목차 바(로고 → 홈), 키·색인 사전 점검 (F2012) |
| | `ui/views/home.py` | 홈: 제목·안내 → 감염병 검색창 → 지구본. 나라를 누르면 지구본이 왼쪽으로 비키고 오른쪽에 검역감염병 팝업 + '상세보기' |
| | `ui/views/chat.py` | 감염병 질문하기: 큰 대화창(질문 속 감염병 자동 인식, 근거·출처) + 오른쪽 대화 요약, 이런 감염병 찾기 (F2009, F2011) |
| | `ui/views/detail.py` | 감염병 상세보기(`/detail?d=inf_0020`): 기본 설명(위키 요약)·급수 기준, 지정 국가, 연령·성별 인포그래픽, 유행 시기, 페이지 안 챗봇 |
| | `ui/views/age.py` | 연령별 알아보기: 연령대별 상위 감염병, 감염병별 연령 분포, 유행 시기 |
| | `ui/common.py` | 페이지 공통: 런타임, 데이터, 페이지 이동, 검색창, 질문 속 감염병 찾기 |
| | `ui/chat_core.py` | 챗봇 부품(질문하기·상세보기 공용): 답변 스트리밍, 카테고리 카드, 걸린 시간, 근거 |
| 측정 | `scripts/bench_chat.py` | 속도 설정·모델별 답변 시간 비교 → `reports/bench_chat.md` |
| | `ui/map_view.py` | 끌어서 돌리는 지구본(plotly orthographic), 고른 나라 쪽으로 회전, 지정 없는 나라도 클릭 가능 |
| | `ui/styles.py` | 다크 테마, 나라 패널이 천천히 떠오르는 애니메이션, 막대 자라기·선 드러나기 애니메이션 |
| 설정 | `config/country_centroids.json` | 나라 중심 좌표 (지구본 회전용, 지도 경계 파일에서 계산) |
| | `ui/static/topojson/` | 지도 경계 파일 (CDN이 막힌 망에서도 지도가 보이도록 앱과 함께 제공) |
| 서비스 | `service/countries.py` | 지도용 나라 목록, `data/processed/country_watch.json` 읽기 |
| 데이터 준비 | `data_prep/quarantine.py`, `scripts/fetch_quarantine.py` | 검역관리지역 API·CSV → 현재 지정만 → 나라·감염병 매핑 → country_watch.json ([분석](docs/QUARANTINE_API.md)) |
| 데이터 준비 | `data_prep/age_stats.py`, `xlsx_lite.py`, `scripts/build_age_stats.py` | 감염병포털 통계 CSV·xlsx → 연령대·성별 분포, 주별 유행 추이 → age_stats.json ([분석](docs/AGE_STATS.md)) |
| 서비스·UI | `service/age.py`, `ui/age_view.py` | 연령대별 상위 감염병, 연령 분포, 유행 시기 계산과 차트 |
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
| 나라별 검역감염병 | [질병관리청_검역관리지역정보](https://www.data.go.kr/data/15139251/openapi.do) | 이용허락범위 제한 없음, 화면에 기준일·출처 표시 |
| 연령별·시기별 통계 | [질병관리청 감염병포털](https://dportal.kdca.go.kr) 기본통계·표본감시 | 공공누리 유형 확인 후 기록, 화면에 출처·신고 건수 기준 표시 |
| 지도 경계 | [sane-topojson](https://github.com/etpinard/sane-topojson) (Natural Earth) | MIT / public domain |

## 7. 변경 이력

| 날짜 | 내용 |
| --- | --- |
| 2026-10-22 | 단계 1: 법정감염병 목록 + 위키백과 RAG (LangGraph 2노드, Chroma 2컬렉션), 질의응답·조건 탐색 화면 |
| 2026-10-22 | 화면 개편: 상단 내비게이션, 한 페이지 3섹션, 클릭 가능한 세계 지도 (지도 경계 파일 동봉) |
| 2026-10-22 | 단계 3: 검역관리지역 API(`/QuarantineRegion/NationDisease`) 연동, 176개국 지도 표시, 작은 섬나라 점 표시, `--raw` 재처리 |
| 2026-10-22 | 단계 2: 연령별 알아보기 (연령대별 상위 감염병, 감염병별 연령·성별 분포, 유행 시기), 통계 파일 하위 폴더 탐색 |
| 2026-10-22 | 최종 실측: 질문당 평균 18.48s → 4.57s(75% 단축, background+문서 미리 색인), 첫 글자 warm 1.1s, LLM 5회 → 1회. 앱 시작 때 임베딩 예열(첫 근거 검색 2.4s 제거), 측정 스크립트가 배경 수집을 기다린 뒤 정리 |
| 2026-10-22 | 실측 반영(GMS): 기존 35.9s → 병렬 24.4s, 수집 생략 warm 2.4s(첫 글자 1.4s). background의 첫 질문은 항목 문서만 바로 색인하고 답(LLM 1회), 측정 스크립트가 사용 한도 초과(429)에도 결과 저장·`--quick`, 화면에 한도 초과 안내 |
| 2026-10-22 | 챗봇 속도·형식: 단계별 시간·호출 수 기록과 화면 표시, 배경 수집·병렬 수집·스트리밍·같은 질문 저장·요약 LLM 생략, 측정 스크립트(`scripts/bench_chat.py`, 모의 측정 평균 5.60s → 3.33s), 답변을 카테고리 카드로. UI: 로고 → 홈, 목차 상시 표시, 상세보기 안에서 챗봇(큰 검색창), 질문하기 페이지 감염병 이름 크게·글자 줄임 |
| 2026-10-22 | 페이지 분리: 홈(인트로 애니메이션 → 제목·안내·검색창·지구본, 나라 팝업과 '상세보기'), 감염병 상세보기(설명·KPI·남녀 막대·나이별 칸·인구 피라미드·유행 시기·지정 국가·챗봇 검색창, 애니메이션), 감염병 질문하기(대화창 + 대화 요약, 질문 속 감염병 자동 인식, 대화 하나로 이어짐), 로고 호버 목차 바 |
| 2026-10-22 | UI 개편: 다크 모드, 나라별 섹션을 맨 위로, 돌릴 수 있는 3D 지구본(고른 나라로 회전), 주요 여행지 표시 제거(`config/countries.json` 삭제), 나라 패널·감염병 줄이 천천히 나타나는 애니메이션, 연령별 막대·유행 시기 선 애니메이션 (움직임 줄이기 설정 시 끔) |

