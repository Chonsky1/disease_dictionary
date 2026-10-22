"""LLM 출력 파싱: JSON 추출과 근거 번호 해석."""
from __future__ import annotations

import json
import re

_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)```", re.S)
_CITE_RE = re.compile(r"\[\s*근거\s*([\d\s,~\-]+)\]")


def parse_json(text: str):
    """코드 블록·앞뒤 설명이 섞여도 첫 JSON 객체/배열을 꺼낸다. 실패하면 ValueError."""
    if not isinstance(text, str):
        text = str(text)
    m = _FENCE_RE.search(text)
    candidates = [m.group(1)] if m else []
    candidates.append(text)
    for c in candidates:
        c = c.strip()
        for opener, closer in (("{", "}"), ("[", "]")):
            start, end = c.find(opener), c.rfind(closer)
            if start != -1 and end > start:
                try:
                    return json.loads(c[start:end + 1])
                except json.JSONDecodeError:
                    continue
    raise ValueError("JSON을 찾을 수 없습니다.")


def _expand(spec: str) -> list[int]:
    nums: list[int] = []
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        rng = re.split(r"\s*[~\-]\s*", part)
        if len(rng) == 2 and rng[0].isdigit() and rng[1].isdigit():
            a, b = int(rng[0]), int(rng[1])
            nums.extend(range(a, b + 1) if a <= b and b - a < 50 else [a, b])
        elif part.isdigit():
            nums.append(int(part))
    return nums


def cited_numbers(answer: str, max_n: int) -> list[int]:
    """답변 본문의 [근거 n], [근거 1, 3], [근거 2~4] 표기를 처음 등장한 순서대로. 범위 밖 번호는 버린다."""
    seen: dict[int, None] = {}
    for m in _CITE_RE.finditer(answer or ""):
        for n in _expand(m.group(1)):
            if 1 <= n <= max_n:
                seen.setdefault(n, None)
    return list(seen)
