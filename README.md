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
python -m unittest discover -s tests -t .   # 키·네트워크 없이 105개 테스트
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
python -m scripts.build_index                  # (위 두 파일을 만든 뒤 한 번 더) 공공데이터 문서를 위키 문서와 함께 색인 (F2016)
streamlit run ui/app.py                        # 반드시 프로젝트 폴더에서 실행 (.streamlit/config.toml 적용)
```

색인 후 `data/processed/wiki_titles.json`에서 `"method": "search"` 또는 `"not_found"`인 항목을 확인하세요.
잘못 잡힌 문서는 `config/wiki_title_overrides.json`에 `"CSV 감염병명": "위키 문서 제목"`으로 적고
`python -m scripts.build_index --refresh-titles`를 다시 실행합니다.

## 챗봇 없이 실행 (키 없이 지도·통계·상세보기만)

GMS 키·위키 User-Agent·색인 없이 **나라별 주의 감염병(지구본)**, **연령별 알아보기**, **감염병 상세보기**만 띄웁니다.

```bash
pip install -r requirements.txt
KDCA_NO_CHAT=1 streamlit run ui/app.py          # Git Bash / macOS / Linux
# PowerShell: $env:KDCA_NO_CHAT="1"; streamlit run ui/app.py
```

| 화면 | 챗봇 없는 모드에서 |
| --- | --- |
| 목차 | '감염병 질문하기' 숨김, 키 누락·색인 없음 점검 건너뜀 |
| 홈 검색창 | 질문 대신 **감염병 이름 검색** → 상세보기로 이동 |
| 나라 팝업 | 검역감염병 목록·상세보기만 ('여행 전 안내'는 LLM이 필요해 숨김) |
| 상세보기 | 급수·신고 기준, 지정 국가, 연령·성별 인포그래픽, 유행 시기. 위키 요약은 **이미 받아 둔 본문이 있을 때만**, 챗봇 칸 없음 |

화면에 쓰는 데이터는 키 없이 준비할 수 있습니다 (없으면 해당 칸에 '수집 전' 안내).

| 데이터 | 키 없이 준비하는 방법 |
| --- | --- |
| 법정감염병 133종 | 저장소에 포함 (`data/seed/`) |
| 나라별 검역감염병 `data/processed/country_watch.json` | 공공데이터포털 '검역관리지역정보' **파일데이터**를 내려받아 `python -m scripts.fetch_quarantine --csv <파일>` (API 방식만 키 필요), 또는 이미 만든 PC에서 파일 복사 |
| 연령별·시기별 통계 `data/processed/age_stats.json` | 감염병포털 통계 CSV·xlsx를 `data/raw/stats/`에 넣고 `python -m scripts.build_age_stats` |
| 상세보기 위키 요약 | `data/raw/wiki/pages/`·`data/processed/wiki_titles.json`을 이미 만든 PC에서 복사 (없으면 요약 칸 생략) |

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
| 서비스 | `service/explore.py` | 조건 기반 탐색: 급수·신고 범위 필터 → 유사도 후보 → LLM 선별 → 결과 재대조 (F2011) |
| | `service/travel.py` | 여행 전 안내: 나라의 검역감염병별 근거 모아 정리 (F2010) |
| 데이터 준비 | `data_prep/public_docs.py` | 검역관리지역·감염병포털 통계 → 감염병별 레코드 문서 (F2016) |
| 검증 | `scripts/verify_checklist.py`, `capture_screens.py`, `compare_rag.py` 외 | 셀프 체크리스트 27항목 실행·증빙·xlsx 기입, 화면 캡처, RAG 전후 비교 |
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

## 기능 (필수 / 심화)

| 구분 | 기능 |
| --- | --- |
| 필수 F2001~F2012 | 133개 법정감염병 메타데이터 색인, 질문마다 위키백과 검색어 추출(프롬프트 1)·후보 선별(프롬프트 2)·수집 캐시, 근거 기반 답변(프롬프트 3, LangGraph 2노드), 근거 번호·출처, 감염병별 대화, 조건 탐색, 진행 상태·키/색인 안내·면책 |
| 특화 F2010 | 홈 지구본(나라별 검역감염병) → 나라 팝업의 **여행 전 안내**(전파 경로·예방·귀국 후 할 일, 근거 포함), 연령별·시기별 통계, 감염병 상세보기 |
| 심화 F2014 | MMR 검색 (`search_type: mmr`, 후보 30 → 다양성 선별) |
| 심화 F2015 | RAG 전후 답변 비교 → [docs/RAG_COMPARISON.md](docs/RAG_COMPARISON.md) (`python -m scripts.compare_rag`) |
| 심화 F2016 | 위키백과 + 공공데이터 API(검역관리지역) + 공공 통계(감염병포털)를 `disease_docs`에 함께 색인, 근거에 출처 종류 배지 |
| (선택 안 함) F2013 | HTML·CSS·JS 프론트엔드 — 화면은 Streamlit |

문서: [요구사항 정의서](docs/REQUIREMENTS.md) · [RAG 파이프라인 설계문서](docs/RAG_DESIGN.md) · [속도 개선](docs/PERFORMANCE.md)

## 4. 컬렉션과 청크 규칙

| 컬렉션 | 단위 | 청크 ID | 주요 메타데이터 |
| --- | --- | --- | --- |
| `disease_meta` | 감염병 1건 = 1청크 | `inf_0021` (재색인해도 덮어쓰기) | grade, group, kcd, scope_*, wiki_title, source_url |
| `disease_docs` | 위키 본문 청크 (글자 수 청킹) + 공공데이터 레코드 (레코드 = 청크) | `inf_0021:wiki-12345:0003`, `inf_0057:kdca-quarantine-inf_0057:0000` | disease_id, doc_id, title, section, source_type(wiki/public_api/public_stats), source_label, source_url, license, query, collected_at |

외부 문서 청크 앞에는 `[감염병: 홍역 | 문서: … | 섹션: 증상]` 머리말을 붙여, 잘린 조각도 어느 감염병 이야기인지 검색에 걸리게 했습니다.

## 5. 검증 명령 (명세서 4장)

| 확인 항목 | 명령 |
| --- | --- |
| 4.1 로드 건수 = 색인 건수, 두 번 실행해도 불변, 색인 텍스트 | `python -m scripts.build_index` 2회 → `python -m scripts.inspect_index --disease 홍역` |
| 4.2 프롬프트 1 검색어, 선별/버린 문서, 재질문 시 캐시 | `python -m scripts.ask --disease 홍역 "홍역은 어떻게 전파되나요?"` 2회 (검증용 기본 `--policy always`: 질문마다 검색어·선별 결과를 바로 출력, 저장 답변 끔) |
| 4.2 앱 기본값(배경 수집)에서의 수집 결과 | `python -m scripts.ask --disease 홍역 --policy background "…"` → 답변 뒤 `[배경 수집]` 줄. 화면에서는 같은 감염병의 다음 답변 진행 상태에 "지난 질문 뒤 배경 수집 완료 · 검색어(프롬프트 1) …", "새로 색인한 문서(프롬프트 2 선별) …" 표시 |
| 4.2 청크 설정 변경 시 청크 수 | `python -m scripts.build_index --rechunk --chunk-size 500 --chunk-overlap 100` |
| 4.3 Top-k 변경, MMR 비교 | `python -m scripts.ask --disease 홍역 --top-k 8 --search-type mmr --no-collect "…"` |
| 4.3 2노드 그래프 구조 | `python -m scripts.inspect_index` (Mermaid 출력) |
| 4.3 문서에 없는 질문 | `python -m scripts.ask --disease 홍역 "홍역 치료제 가격은 얼마인가요?"` |
| 4.4 화면 | `streamlit run ui/app.py`, 키를 비운 채 실행, 색인 폴더(`data/chroma`)를 지운 채 실행 |

자동 테스트(`tests/test_stage1.py`, `tests/test_ui.py`)가 위 항목 대부분을 가짜 LLM·가짜 위키로 확인합니다.

### 셀프 체크리스트 자동 검증·증빙

```bash
pip install -r requirements-dev.txt && python -m playwright install chromium   # 처음 한 번 (화면 캡처용)
python -m scripts.verify_checklist --xlsx "셀프체크리스트.xlsx" --author "이름"   # 실제 모델로 27항목 실행
python -m scripts.verify_checklist --xlsx "셀프체크리스트.xlsx" --offline        # 키 없이 도구만 시험 (제출용 아님)
python -m scripts.verify_checklist --xlsx "셀프체크리스트.xlsx" --only 1,2       # 일부 항목만 다시 (나머지는 지난 결과 유지)
python -m scripts.verify_checklist --xlsx "셀프체크리스트.xlsx" --fill-only      # 다시 실행하지 않고 지난 결과로 xlsx만
python -m scripts.verify_checklist --xlsx "셀프체크리스트.xlsx" --fill-only --import-log run1.txt --import-log run2.txt  # 콘솔 출력 파일에서 복원
```

결과: `submission/evidence/`(항목별 실행 출력 `NN_*.txt/png`, 화면 캡처 `S08_*.png` …), `submission/셀프체크리스트_결과.xlsx`(결과·증빙·비고 기입),
`submission/검증결과/`(요구사항 대조표, 테스트 결과표, 검증 요약). 항목별 명령만 따로 돌릴 수도 있습니다.

| 체크리스트 | 명령 |
| --- | --- |
| 11 조건 탐색 결과 대조 | `python -m scripts.explore_check` |
| 12 특화 기능 근거 | `python -m scripts.travel_check --country VNM` |
| 17·18 RAG 전후 비교 | `python -m scripts.compare_rag` |
| 21 키 노출 검사 | `python -m scripts.scan_secrets submission` |
| 화면 캡처만 | `python -m scripts.capture_screens --out submission/evidence` |

F2005 확인은 청크 설정을 500/100으로 바꿔 재색인한 뒤 원래 설정으로 되돌립니다(임베딩 호출 발생).

## 6. 출처와 이용 조건 (NF2007)

전체 등록부는 `config/sources.json`(항목마다 `license`·`license_url`·`checked_at`·`used`)에 있고, 화면 캡션은 이 파일에서 이용 조건을 읽어 표시합니다(`data_prep/sources.py`).

| 데이터 | 출처 | 이용 조건 | 화면 표시 |
| --- | --- | --- | --- |
| 나라별 검역감염병 | [질병관리청_검역관리지역정보](https://www.data.go.kr/data/15139251/openapi.do) | 이용허락범위 제한 없음 (2026-10-22 확인) | 홈·상세보기에 기준일·출처·조건 |
| 연령별·성별 통계, 질병별 발생현황 | [질병관리청 감염병포털](https://dportal.kdca.go.kr) 통계 | [공공누리 제4유형](https://www.kogl.or.kr/info/licenseType4.do) 출처표시·상업적 이용금지·변경금지 (2026-10-22 확인) | 차트마다 출처·유형·"원자료 수치를 바꾸지 않고 집계·시각화" |
| 표본감시 통계 (4급, SARI·인플루엔자 등) | 감염병포털 표본감시 | 공공누리 제4유형 (2026-10-22 확인) | 위와 같음 |
| 감염병 설명 문서 | [한국어 위키백과](https://ko.wikipedia.org) | [CC BY-SA 4.0](https://creativecommons.org/licenses/by-sa/4.0/deed.ko) | 근거마다 문서 제목·원문 링크, 상세보기 설명 아래 출처 |
| 법정감염병 목록 133건 | [감염병포털 법정감염병 분류](https://dportal.kdca.go.kr/pot/ii/sttyInftnsds/sttyInftnsds.do) | 공공누리 제4유형 (2026-10-22 확인) | 상세보기 '제N급이란?' 아래 출처·조건. 이름·급·KCD·신고 범위를 원문 그대로 옮겨 씀 |
| 지도 경계 | [sane-topojson](https://github.com/etpinard/sane-topojson) (Natural Earth) | MIT / 퍼블릭 도메인 | - |

- **변경금지(제4유형) 대응**: 원자료의 수치는 고치지 않고 연령대·성별·주 단위 합계, 순위, 비율만 계산해 차트로 보여 줍니다. 화면마다 원출처와 유형을 밝히고, 비상업 교육 목적(SSAFY 프로젝트)으로만 사용합니다. 내려받은 원본 파일(`data/raw/stats`)은 저장소에 올리지 않습니다.
- **위키백과(CC BY-SA 4.0)**: 저작자 표시는 근거 목록의 제목·링크로 합니다. 수집한 본문(`data/raw/wiki`)은 저장소에 올리지 않으며, 다시 배포할 때는 같은 라이선스를 붙입니다(동일조건변경허락).
- 등록부에 있지만 쓰지 않는 후보(전수신고 발생현황 API, 예방접종 정보, 인플루엔자·RSV 검출률)는 `"used": false`로 표시했습니다.

## 7. 변경 이력

| 날짜 | 내용 |
| --- | --- |
| 2026-10-24 | 챗봇 없이 실행(`KDCA_NO_CHAT=1`): 키·위키·색인 없이 지도·연령별·상세보기만, 검색창은 감염병 이름 검색, 질문하기·여행 전 안내·상세보기 챗봇 숨김 (테스트 추가) |
| 2026-10-23 | 검증 도구: 체크리스트 맨 아래 상호 점검 피드백 칸이 채워져 있으면 27번(제출물)에서 상호 점검으로 인정, `--fill-only`로 다시 만들어도 직접 쓴 피드백·점검자 칸은 유지 |
| 2026-10-23 | 수정: 한 감염병에 이름이 다른 통계 항목이 여럿(예: 엔테로바이러스 아형)이면 공공데이터 문서 ID가 겹쳐 새 색인에서 `DuplicateIDError` → 항목 이름을 ID에 넣고 같은 청크 ID는 하나만 넣음. 색인 테스트가 실제 수집 데이터를 읽지 않게 격리, `--fill-only`도 자동 테스트를 다시 실행 |
| 2026-10-23 | 검증 도구: `--import-log`로 저장해 둔 콘솔 출력에서 결과 복원(증빙 파일은 evidence 폴더에서 다시 연결), `--fill-only`는 키 없이 실행 |
| 2026-10-23 | 검증 도구 보완: 체크리스트 파일 경로를 실행 전에 확인(없으면 비슷한 이름을 찾아 씀), 결과 저장(`검증결과/results.json`)과 `--only` 재실행 시 지난 결과 유지·`--fill-only`, 색인 실패 시 원인 한 줄 표시·한 번 재시도. 색인: 내용이 같은 메타데이터·공공데이터 문서는 다시 임베딩하지 않고, 임베딩을 100개씩 묶어 요청(재실행이 빨라지고 API 일시 오류에 덜 노출) |
| 2026-10-23 | `requirements-dev.txt` 주석을 영문으로 (Windows pip이 cp949로 읽어 설치 실패하던 문제), 이를 검사하는 테스트 |
| 2026-10-23 | 셀프 체크리스트 대응: 검증·증빙 자동 생성(`verify_checklist`, `capture_screens`, `explore_check`, `travel_check`, `scan_secrets`), 오프라인 모의 실행(`KDCA_OFFLINE_DEMO`, `KDCA_DATA_DIR`). 심화 F2016 공공데이터 레코드 문서 색인·근거 출처 종류 배지, F2010 여행 전 안내(근거 포함), F2015 RAG 전후 비교(`compare_rag`), F2011 급수·신고 범위 필터와 결과 재대조. 요구사항 정의서·RAG 설계문서 |
| 2026-10-22 | 출처 확인 완료(NF2007): 법정감염병 분류·표본감시 통계도 공공누리 제4유형, 사용하는 출처 모두 확인일 기록(테스트로 검사), 상세보기에 분류 출처 캡션 |
| 2026-10-22 | 출처·이용 조건 정리(NF2007): 검역관리지역정보 '이용허락범위 제한 없음', 감염병포털 통계 공공누리 제4유형 기록, `sources.json`에 `license_url`·`checked_at`·`used`, 화면 캡션에 이용 조건과 집계 문구 표시, 등록부 검사 테스트 |
| 2026-10-22 | 요구사항 보완: 감염병 질문하기 페이지에서 감염병별 대화 기록 따로 보관(F2009, 다른 감염병으로 바꿔도 이전 대화 유지, '대화 지우기'는 현재 감염병만), 대화 요약에 '대화한 감염병' 이동 버튼. 배경 수집 결과(검색어·새로 색인한 문서·버린 후보)를 다음 답변 진행 상태에 표시(F2003·F2004·F2012), `scripts.ask --policy`(기본 always)·`--use-cache` |
| 2026-10-22 | 단계 1: 법정감염병 목록 + 위키백과 RAG (LangGraph 2노드, Chroma 2컬렉션), 질의응답·조건 탐색 화면 |
| 2026-10-22 | 화면 개편: 상단 내비게이션, 한 페이지 3섹션, 클릭 가능한 세계 지도 (지도 경계 파일 동봉) |
| 2026-10-22 | 단계 3: 검역관리지역 API(`/QuarantineRegion/NationDisease`) 연동, 176개국 지도 표시, 작은 섬나라 점 표시, `--raw` 재처리 |
| 2026-10-22 | 단계 2: 연령별 알아보기 (연령대별 상위 감염병, 감염병별 연령·성별 분포, 유행 시기), 통계 파일 하위 폴더 탐색 |
| 2026-10-22 | 챗봇 말투를 '~입니다/~합니다' 체로 (답변·대화 요약·조건 탐색 이유), 답변 프롬프트 v3로 예전 말투의 저장 답변은 다시 쓰지 않음 |
| 2026-10-22 | 최종 실측: 질문당 평균 18.48s → 4.57s(75% 단축, background+문서 미리 색인), 첫 글자 warm 1.1s, LLM 5회 → 1회. 앱 시작 때 임베딩 예열(첫 근거 검색 2.4s 제거), 측정 스크립트가 배경 수집을 기다린 뒤 정리 |
| 2026-10-22 | 실측 반영(GMS): 기존 35.9s → 병렬 24.4s, 수집 생략 warm 2.4s(첫 글자 1.4s). background의 첫 질문은 항목 문서만 바로 색인하고 답(LLM 1회), 측정 스크립트가 사용 한도 초과(429)에도 결과 저장·`--quick`, 화면에 한도 초과 안내 |
| 2026-10-22 | 챗봇 속도·형식: 단계별 시간·호출 수 기록과 화면 표시, 배경 수집·병렬 수집·스트리밍·같은 질문 저장·요약 LLM 생략, 측정 스크립트(`scripts/bench_chat.py`, 모의 측정 평균 5.60s → 3.33s), 답변을 카테고리 카드로. UI: 로고 → 홈, 목차 상시 표시, 상세보기 안에서 챗봇(큰 검색창), 질문하기 페이지 감염병 이름 크게·글자 줄임 |
| 2026-10-22 | 페이지 분리: 홈(인트로 애니메이션 → 제목·안내·검색창·지구본, 나라 팝업과 '상세보기'), 감염병 상세보기(설명·KPI·남녀 막대·나이별 칸·인구 피라미드·유행 시기·지정 국가·챗봇 검색창, 애니메이션), 감염병 질문하기(대화창 + 대화 요약, 질문 속 감염병 자동 인식, 대화 하나로 이어짐), 로고 호버 목차 바 |
| 2026-10-22 | UI 개편: 다크 모드, 나라별 섹션을 맨 위로, 돌릴 수 있는 3D 지구본(고른 나라로 회전), 주요 여행지 표시 제거(`config/countries.json` 삭제), 나라 패널·감염병 줄이 천천히 나타나는 애니메이션, 연령별 막대·유행 시기 선 애니메이션 (움직임 줄이기 설정 시 끔) |

