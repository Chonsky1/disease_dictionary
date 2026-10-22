"""감염병마다 '항목 자체를 다룬 위키 문서'의 제목을 정하고 파일로 고정한다.

이 문서는 F2003·F2004에 따라 질문과 상관없이 항상 수집·색인된다.
해석 순서: 수동 지정(config/wiki_title_overrides.json) → 제목 후보(page.exists, 리다이렉트 포함) → 검색 1순위.
검색으로 정한 제목은 method="search"로 남겨 사람이 확인하게 한다.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Callable

from .diseases import Disease
from .wiki import WikiClient


def load_overrides(path: Path) -> dict[str, str]:
    p = Path(path)
    if not p.exists():
        return {}
    return {k: v for k, v in json.loads(p.read_text(encoding="utf-8")).items() if not k.startswith("_")}


def resolve_one(d: Disease, client: WikiClient, overrides: dict[str, str]) -> dict:
    if d.name in overrides:
        r = client.resolve(overrides[d.name])
        if r:
            return {"title": r["title"], "pageid": r["pageid"], "url": r["url"], "method": "override"}
    for cand in d.wiki_title_candidates():
        r = client.resolve(cand)
        if r and not r["disambiguation"]:
            return {"title": r["title"], "pageid": r["pageid"], "url": r["url"],
                    "method": "redirect" if r["redirected"] else "exact"}
    hits = [c for c in client.search_candidates(d.display_name, limit=5) if not c["disambiguation"]]
    if hits:
        return {"title": hits[0]["title"], "pageid": hits[0]["pageid"], "url": hits[0]["url"], "method": "search"}
    return {"title": "", "pageid": 0, "url": "", "method": "not_found"}


def resolve_titles(diseases: list[Disease], client: WikiClient, overrides: dict[str, str],
                   done: dict[str, dict] | None = None,
                   progress: Callable[[int, int, str], None] | None = None) -> dict[str, dict]:
    """{disease_id: {name, title, pageid, url, method}}. done에 이미 있는 항목은 다시 묻지 않는다."""
    out = dict(done or {})
    for i, d in enumerate(diseases, start=1):
        if d.disease_id not in out:
            out[d.disease_id] = {"name": d.display_name, **resolve_one(d, client, overrides)}
        if progress:
            progress(i, len(diseases), d.display_name)
    return out


def save_titles(path: Path, titles: dict[str, dict]) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(titles, ensure_ascii=False, indent=2), encoding="utf-8")


def load_titles(path: Path) -> dict[str, dict]:
    p = Path(path)
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
