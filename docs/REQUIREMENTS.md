# 요구사항 정의서 — 법정감염병 안내 도우미

SSAFY AI 코어프로젝트 2회차 · RAG 서비스 · 저장소 <https://github.com/Chonsky1/disease_dictionary>

## 1. 도메인 선택과 조건 충족 근거

| 조건 | 이 서비스 |
| --- | --- |
| 항목 목록 (메타데이터) | 질병관리청 감염병포털 법정감염병 목록 **133건** (`data/seed/infectious_diseases.csv`: 급수, 감염병명, KCD 코드, 신고 범위(환자·의사환자·병원체보유자), 담당부서) |
| 항목별 외부 문서 | 한국어 위키백과 (질문마다 검색어로 수집, 항목 자체 문서는 미리 색인 가능) |
| 다른 종류의 문서 출처 (심화 F2016) | 공공데이터 API(질병관리청 검역관리지역정보), 공공 통계(감염병포털 성별·연령별·표본감시) → 감염병별 레코드 문서 |
| 출처와 이용 조건 | README 6절, `config/sources.json` (공공누리 제4유형, 이용허락범위 제한 없음, CC BY-SA 4.0, MIT) |
| 판단이 위험한 도메인 | 건강 → 모든 답변 화면·홈에 "전문가의 진단이나 조언을 대신하지 않습니다" 안내, 개인 진단·용량 질문은 1339·의료기관 안내 |

### 근거가 필요한 질문 예시 (근거 없이 답하면 틀리기 쉬움)

| 질문 | 왜 근거가 필요한가 | 근거 |
| --- | --- | --- |
| 코로나바이러스감염증-19는 현재 몇 급인가요? | 제2급에서 제4급으로 하향 (모델 학습 시점에 따라 제2급으로 답하기 쉬움) | 항목 메타데이터 |
| 엠폭스는 몇 급이고 담당 부서는? | 여러 번 급수가 바뀜, 조직 정보 | 항목 메타데이터 |
| 쯔쯔가무시증의 KCD 코드는? | 외워 두기 어려운 코드 | 항목 메타데이터 |
| 황열 때문에 검역관리지역으로 지정된 나라는? | 날짜에 따라 바뀌는 지정 현황 | 공공데이터 API 문서 |
| 수두는 어느 연령대에서 많이 신고되나요? | 통계 수치 | 공공 통계 문서 |
| 홍역 치료제 가격은? | 문서에 없음 → "확인되지 않습니다"가 정답 | (없음) |

RAG 전후 비교 결과: [RAG_COMPARISON.md](RAG_COMPARISON.md) (`python -m scripts.compare_rag`)

## 2. 기능 요구사항

### 2.1 필수 (F2001 ~ F2012)

| ID | 구분 | 요구사항 | 구현 | 검증 (체크리스트 번호) |
| --- | --- | --- | --- | --- |
| F2001 | 데이터 | 항목 133건을 로드해 1건 = 1청크로 `disease_meta`에 색인. 색인 텍스트에 급수·급수 기준·신고 시기·신고 범위·KCD·담당부서·위키 요약 | `data_prep/diseases.py`, `scripts/build_index.py` | 1 |
| F2002 | 데이터 | 색인을 여러 번 실행해도 청크 수 불변 (청크 ID 고정 + upsert, 문서는 지우고 다시 넣기) | `vectorstore/store.py` | 2 |
| F2003 | 수집 | 프롬프트 1: 선택 항목 정보 + 질문 → 위키 검색어 최대 3개(JSON) | `rag/prompts.py` QUERY_EXTRACTION, `rag/graph.py` | 3 |
| F2004 | 수집 | 프롬프트 2: 후보 제목·요약만 보고 근거로 쓸 문서 선별(JSON), 버린 문서 기록, 검색어 캐시 | `rag/graph.py` make_selector, `data_prep/collector.py` | 3, 4 |
| F2005 | 색인 | 청크 크기·중복 길이를 설정 파일에서 바꿔 재색인 | `config/rag_settings.json`, `build_index --rechunk` | 5 |
| F2006 | 검색 | 선택 항목 범위(disease_id 필터) 안에서 Top-k 검색, 설정으로 변경 | `vectorstore/store.py` search_docs | 6 |
| F2007 | 생성 | LangGraph `retrieve` → `generate` 두 노드, 근거에 없는 내용은 "확인되지 않습니다" | `rag/graph.py`, 프롬프트 3 | 7, 9 |
| F2008 | 표시 | 본문 [근거 n] ↔ 근거 목록 번호 일치, 항목 메타데이터와 외부 문서(종류) 구분, 인용 없으면 안내 | `rag/graph.py` build_evidence, `ui/chat_core.py` | 8, 9 |
| F2009 | 화면 | 목록에서 항목 선택, 항목별 대화 기록 분리 유지 | `ui/views/chat.py` (`chat_logs[disease_id]`) | 10 |
| F2010 | 특화 | **여행 전 안내**: 나라 팝업에서 그 나라 검역감염병의 전파 경로·예방·귀국 후 할 일을 근거와 함께 정리. 나라별 검역감염병 지구본, 연령별·시기별 통계 | `service/travel.py`, `ui/views/home.py`, `ui/views/age.py` | 12 |
| F2011 | 특화 | 자연어 조건 → 메타데이터 필터(급수·신고 범위) + 유사도 후보 → LLM 선별 → 결과 재대조 → 카드에서 질문으로 이어짐 | `service/explore.py`, `ui/views/chat.py` | 11 |
| F2012 | 화면 | 수집·생성 진행 상태, 색인 없음·키 없음 안내, 전문가 조언 대체 아님 안내 | `ui/app.py`, `ui/chat_core.py` | 13, 14 |

### 2.2 심화 (선택: F2014, F2015, F2016 / 선택 안 함: F2013)

| ID | 요구사항 | 구현 | 필수 결과와 비교 | 검증 |
| --- | --- | --- | --- | --- |
| F2013 | HTML·CSS·JS 프론트엔드 | 선택하지 않음 (화면은 Streamlit) | - | 15 (해당없음) |
| F2014 | MMR 검색: 후보 30건 → 다양성으로 Top-k | `search_type: mmr`, `mmr_fetch_k: 30`, `mmr_lambda: 0.5` | 같은 질문의 유사도 검색 근거 목록과 나란히 (`evidence/16_*`) | 16 |
| F2015 | RAG 전후 답변 비교 (대표 질문 5개 이상) | `scripts/compare_rag.py` | RAG 없는 답과 사실 정확성·근거 제시·모름 표명 비교 → `docs/RAG_COMPARISON.md` | 17, 18 |
| F2016 | 종류가 다른 문서 출처 결합 | `data_prep/public_docs.py`: 공공데이터 API·공공 통계를 레코드 문서로 `disease_docs`에 함께 색인, 근거에 종류 배지 | 위키 단일 출처일 때는 답할 수 없던 지정 국가·연령 통계 질문에 근거 제시 | 19 |

## 3. 비기능 요구사항

| ID | 요구사항 | 구현 | 검증 |
| --- | --- | --- | --- |
| NF2001 | 대표 질문 처리 시간이 사용에 지장 없음 | 배경 수집·병렬 수집·스트리밍·문서 미리 색인: 평균 18.5s → 4.6s, 첫 글자 약 1.1s ([PERFORMANCE.md](PERFORMANCE.md)) | 20 |
| NF2002 | 이미 수집한 검색어는 다시 수집하지 않음 | `data/cache/query_cache.json`, 본문 `data/raw/wiki/pages/<pageid>.json` | 4 |
| NF2003 | API 키는 `.env`(git 제외)에서만 | `.env.example`, `.gitignore`, `scripts/scan_secrets.py` | 21 |
| NF2004 | 화면에 키·프롬프트 전문·오류 추적 정보 노출 없음 | 오류는 `logs/app.log`에만, 화면엔 안내 문장 | 21 |
| NF2005 | 수집·색인·검색·생성·UI 모듈 분리 | `data_prep` / `vectorstore` / `rag` / `service` / `ui` | 22 |
| NF2006 | 외부 호출 간격·재시도·식별 정보 | 위키: 최소 간격 0.5s, 재시도 3회, maxlag=5, 연락처 든 User-Agent 필수 / 공공데이터: 간격·재시도·키 마스킹 | 23 |
| NF2007 | 데이터·문서 출처와 이용 조건 기록 | README 6절, `config/sources.json`, 화면 캡션 | 24 |

## 4. 검증 방법

`python -m scripts.verify_checklist --xlsx <체크리스트.xlsx>` 가 체크리스트 27항목을 실제 명령으로 실행하고,
증빙(`submission/evidence/`), 체크리스트 결과 사본, 요구사항 대조표·테스트 결과표(`submission/검증결과/`)를 만든다.
