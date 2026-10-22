"""아주 작은 xlsx 리더 (표준 라이브러리만 사용).

감염병포털에서 내려받은 통계 xlsx는 시트 1개, 서식 없는 값만 있어서 openpyxl 없이 읽을 수 있다.
병합 셀은 포털이 같은 값을 칸마다 반복해 써 두므로 따로 처리하지 않는다.
"""
from __future__ import annotations

import re
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

NS = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
_REF = re.compile(r"([A-Z]+)(\d+)")


def _col_index(letters: str) -> int:
    n = 0
    for ch in letters:
        n = n * 26 + (ord(ch) - 64)
    return n - 1


def _text(el) -> str:
    return "".join(t.text or "" for t in el.iter(f"{{{NS['m']}}}t"))


def read_xlsx(path: Path, sheet: int = 1) -> list[list[str]]:
    """첫 시트를 문자열 격자로 (빈 칸은 ""). 행·열 번호는 엑셀과 같게 0부터 (A1 → [0][0])."""
    with zipfile.ZipFile(path) as z:
        shared = []
        if "xl/sharedStrings.xml" in z.namelist():
            root = ET.fromstring(z.read("xl/sharedStrings.xml"))
            shared = [_text(si) for si in root.findall("m:si", NS)]
        root = ET.fromstring(z.read(f"xl/worksheets/sheet{sheet}.xml"))
    cells: dict[tuple[int, int], str] = {}
    for c in root.iter(f"{{{NS['m']}}}c"):
        m = _REF.fullmatch(c.get("r", ""))
        if not m:
            continue
        t = c.get("t")
        v = c.find("m:v", NS)
        if t == "s" and v is not None:
            val = shared[int(v.text)]
        elif t == "inlineStr":
            val = _text(c)
        else:
            val = v.text if v is not None and v.text is not None else ""
        cells[(int(m.group(2)) - 1, _col_index(m.group(1)))] = val
    if not cells:
        return []
    n_rows = max(r for r, _ in cells) + 1
    n_cols = max(c for _, c in cells) + 1
    grid = [[""] * n_cols for _ in range(n_rows)]
    for (r, c), val in cells.items():
        grid[r][c] = val
    return grid


def write_xlsx(path: Path, grid: list[list[str]]) -> None:
    """테스트용: 문자열 격자를 최소 xlsx로 저장 (inline string)."""
    def ref(r: int, c: int) -> str:
        s, c = "", c + 1
        while c:
            c, rem = divmod(c - 1, 26)
            s = chr(65 + rem) + s
        return f"{s}{r + 1}"

    def esc(s: str) -> str:
        return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    rows = "".join(
        f'<row r="{r + 1}">' + "".join(f'<c r="{ref(r, c)}" t="inlineStr"><is><t>{esc(v)}</t></is></c>'
                                       for c, v in enumerate(row) if v != "") + "</row>"
        for r, row in enumerate(grid))
    sheet = (f'<?xml version="1.0" encoding="UTF-8"?><worksheet xmlns="{NS["m"]}"><sheetData>{rows}</sheetData></worksheet>')
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("xl/worksheets/sheet1.xml", sheet)
