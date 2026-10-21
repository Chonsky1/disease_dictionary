"""감염병포털·공공데이터포털 CSV를 안전하게 읽는다.

포털 CSV는 cp949 인코딩, 표 위의 제목 줄, 천 단위 쉼표, '-'(신고 대상 아님)와
'0'(발생 없음)의 구분 같은 함정이 있어 읽기 단계에서 정리해 둔다.
"""
from __future__ import annotations

import csv
import io
import re
from collections import Counter
from pathlib import Path

ENCODINGS = ("utf-8-sig", "cp949", "euc-kr")
NOT_APPLICABLE = {"-", "－", "—"}  # 신고·보고 대상이 아닌 경우
MISSING = {"", "NA", "N/A", "nan", "NaN"}


def sniff_encoding(path: Path) -> str:
    raw = Path(path).read_bytes()
    for enc in ENCODINGS:
        try:
            raw.decode(enc)
            return enc
        except UnicodeDecodeError:
            continue
    raise UnicodeDecodeError("unknown", raw[:20], 0, 1, f"{path}: 지원 인코딩으로 읽을 수 없음")


def find_header_row(rows: list[list[str]], scan: int = 30) -> int:
    """표 위의 제목·주석 줄을 건너뛰고 헤더 줄 위치를 찾는다.

    가장 흔한 '채워진 칸 수'를 가진 첫 줄을 헤더로 본다.
    """
    widths = [sum(1 for c in r if c.strip()) for r in rows[:scan]]
    if not widths:
        return 0
    common = Counter(w for w in widths if w > 1).most_common(1)
    target = common[0][0] if common else max(widths)
    for i, w in enumerate(widths):
        if w == target:
            return i
    return 0


def to_number(value: str):
    """'1,234' → 1234.0, '-' → None(해당 없음), '' → None. 숫자가 아니면 원래 문자열."""
    v = str(value).strip()
    if v in NOT_APPLICABLE or v in MISSING:
        return None
    cleaned = re.sub(r"[,\s]", "", v)
    try:
        return float(cleaned)
    except ValueError:
        return value


def read_table(path: Path) -> tuple[list[str], list[list[str]], dict]:
    """표준 라이브러리만으로 읽기 (서비스 코드용). (헤더, 데이터 행, 메타) 반환."""
    enc = sniff_encoding(path)
    text = Path(path).read_text(encoding=enc)
    rows = [r for r in csv.reader(io.StringIO(text))]
    h = find_header_row(rows)
    header = [c.strip() for c in rows[h]]
    data = [r for r in rows[h + 1:] if any(c.strip() for c in r)]
    meta = {"encoding": enc, "header_row": h, "preamble": [" ".join(r).strip() for r in rows[:h]]}
    return header, data, meta


def load_dataframe(path: Path):
    """EDA용 pandas DataFrame. 원문 문자열을 보존하고, 숫자 변환은 분석 단계에서 한다."""
    import pandas as pd  # EDA 전용 의존성

    header, data, meta = read_table(path)
    width = len(header)
    data = [(r + [""] * width)[:width] for r in data]
    df = pd.DataFrame(data, columns=_dedupe(header))
    return df, meta


def _dedupe(cols: list[str]) -> list[str]:
    seen: Counter = Counter()
    out = []
    for c in cols:
        c = c or "unnamed"
        seen[c] += 1
        out.append(c if seen[c] == 1 else f"{c}_{seen[c]}")
    return out
