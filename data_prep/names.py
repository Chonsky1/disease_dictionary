"""감염병 이름 정규화와 시드 목록과의 조인.

포털 목록, 통계 CSV, 위키백과 제목이 같은 감염병을 다르게 적는 경우가 많다
(띄어쓰기, 괄호 속 영문 약어, '감염증'/'증' 차이). 정규화 키로 맞추고,
그래도 안 맞는 것은 config/name_aliases.json에 손으로 적는다.
"""
from __future__ import annotations

import difflib
import json
import re
import unicodedata
from pathlib import Path


def name_key(name: str) -> str:
    s = unicodedata.normalize("NFKC", str(name))
    s = re.sub(r"\(.*?\)|\[.*?\]", "", s)      # 괄호 속 약어·설명 제거
    s = re.sub(r"[\s·ㆍ・\-_/,.]", "", s)
    return s.lower()


def load_aliases(path: Path) -> dict[str, str]:
    """{별칭: 시드의 정식 이름}. 파일이 없으면 빈 dict."""
    p = Path(path)
    if not p.exists():
        return {}
    data = json.loads(p.read_text(encoding="utf-8"))
    return {name_key(k): v for k, v in data.items() if not k.startswith("_")}


def match_names(source_names, seed_names, aliases: dict[str, str] | None = None):
    """출처 이름 목록을 시드 이름에 맞춘다. (매칭 dict, 미매칭 목록과 후보) 반환."""
    aliases = aliases or {}
    seed_by_key = {name_key(n): n for n in seed_names}
    keys = list(seed_by_key)
    matched, unmatched = {}, []
    for raw in dict.fromkeys(str(n) for n in source_names):  # 순서 유지 중복 제거
        k = name_key(raw)
        if k in seed_by_key:
            matched[raw] = seed_by_key[k]
        elif k in aliases:
            matched[raw] = aliases[k]
        else:
            hints = difflib.get_close_matches(k, keys, n=3, cutoff=0.6)
            unmatched.append({"name": raw, "candidates": [seed_by_key[h] for h in hints]})
    return matched, unmatched
