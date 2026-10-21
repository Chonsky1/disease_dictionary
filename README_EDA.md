# 데이터 확보 및 EDA

법정감염병 안내 도우미의 1일차 작업용 코드다. 목표는 16시 도메인 확정 관문 통과와,
2일차 색인 작업에 필요한 결정(연령대 매핑, 이름 별칭, 청크 크기)을 데이터로 내리는 것이다.

## 준비

```bash
pip install -r requirements-dev.txt
cp .env.example .env          # 공공데이터포털 Decoding 키, 위키 User-Agent 연락처 입력
python -m unittest discover -s tests -t .   # 네트워크 없이 13개 테스트
```

## 순서 (1일차)

| 시각 | 할 일 | 명령 / 위치 | 산출물 |
| --- | --- | --- | --- |
| 오전 | API 3종 활용신청 (승인에 수 시간 걸릴 수 있음) | 공공데이터포털 | 승인 메일 |
| 오전 | 시드 작성: 감염병포털 법정감염병 목록을 옮겨 적기 | data/seed/diseases_seed.csv | 시드 CSV |
| 오전 | 시드 관문 점검 | `python -m scripts.check_seed` | reports/seed_check.md |
| 오전 | 위키백과 커버리지 | `python -m scripts.check_wiki_coverage` | reports/wiki_coverage.md |
| 오후 | CSV 내려받기 → data/raw/csv/ (파일명 규칙은 config/sources.json의 file_glob) | 감염병포털 | 원본 CSV |
| 오후 | CSV 프로파일링 | `python -m scripts.eda_report` | reports/eda_report.md |
| 오후 | 데이터셋별 세부 질문 | eda/explore.py (VS Code 셀 실행) | reports/figs/ |
| 승인 후 | API 원본 수집과 필드 목록 | `python -m scripts.fetch_api --source quarantine_area` | data/raw/api/, reports/api_fields_*.md |
| 16시 | 관문 판정 | seed_check 통과 + 위키 커버리지 90% 이상 | 도메인 확정 |

## 폴더

```
config/        sources.json(출처·이용 조건 등록부), age_groups.json(공통 연령대), name_aliases.json
data/seed/     diseases_seed.csv (손으로 채우는 항목 메타데이터, 1급 12건 예시 포함)
data/raw/      api/<출처>/<날짜>.json, csv/*.csv  (원본은 수정하지 않음)
data/processed wiki_coverage.csv, name_match.csv, age_label_map.csv
data_prep/     서비스에서도 재사용하는 모듈 (requests + 표준 라이브러리, pandas 불필요)
scripts/       1일차 점검·수집 스크립트
eda/           pandas·matplotlib 탐색 (EDA 전용 의존성)
tests/         단위 테스트, fixtures/는 합성 데이터
```

## 알아 둘 함정

- 공공데이터포털 키는 **Decoding** 키를 넣는다. Encoding 키는 이중 인코딩되어 인증 오류가 난다.
- 인증 오류는 JSON이 아니라 XML(OpenAPI_ServiceResponse)로 온다. `kdca_api.extract_items`가 잡아서 알려 준다.
- 포털 CSV는 cp949 인코딩과 표 위 제목 줄이 흔하다. `csv_loader.read_table`이 자동 처리한다.
- `-`는 '신고 대상 아님', `0`은 '발생 없음'이다. 둘을 섞으면 통계 문서가 틀린다. `to_number('-')`는 None을 돌려준다.
- 원본 구간이 공통 연령대 두 개에 걸치면(예: 5-9세) `age_label_map.csv`에 exact=False로 남는다. 이런 구간은 공통 연령대로 합산하지 말고 원본 구간 그대로 인용한다.
- 전수신고 API 데이터는 변경금지 조건이다. 수치를 가공하지 말고 원 수치·기준일·출처와 함께 인용한다.
