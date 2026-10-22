"""외부 문서 수집과 수집 캐시 (F2004, NF2002).

- 검색어 캐시: 한 번 수집한 검색어는 다시 검색·선별·수집하지 않는다.
- 본문 저장소: 받은 본문은 data/raw/wiki/pages/<pageid>.json에 저장해 다시 받지 않는다.
- 후보 선별은 프롬프트 2가 담당하므로 select_fn으로 주입받는다 (프롬프트는 rag 영역).
"""
from __future__ import annotations

import json
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from .wiki import WikiClient

SelectFn = Callable[[list[dict]], list[int]]   # 후보 목록 → 고른 pageid 목록


def normalize_query(q: str) -> str:
    return " ".join(str(q).split()).lower()


@dataclass
class CollectResult:
    query: str
    from_cache: bool
    pages: list[dict] = field(default_factory=list)
    candidates: list[str] = field(default_factory=list)   # 후보 제목
    rejected: list[str] = field(default_factory=list)     # 선별에서 버린 제목


class WikiCollector:
    def __init__(self, client: WikiClient, pages_dir: Path, cache_path: Path,
                 candidates_per_query: int = 5, max_selected: int = 2):
        self.client = client
        self.pages_dir = Path(pages_dir)
        self.cache_path = Path(cache_path)
        self.candidates_per_query = candidates_per_query
        self.max_selected = max_selected
        self._lock = threading.Lock()
        self.pages_dir.mkdir(parents=True, exist_ok=True)
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)

    # ---- 캐시 -------------------------------------------------------------------
    def _load_cache(self) -> dict:
        if self.cache_path.exists():
            return json.loads(self.cache_path.read_text(encoding="utf-8"))
        return {}

    def _save_cache(self, cache: dict) -> None:
        tmp = self.cache_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self.cache_path)

    def is_cached(self, query: str) -> bool:
        return normalize_query(query) in self._load_cache()

    # ---- 본문 ------------------------------------------------------------------
    def get_page(self, pageid: int, title: str) -> dict:
        """저장된 본문이 있으면 그것을, 없으면 제목으로 받아 pageid 이름으로 저장한다."""
        path = self.pages_dir / f"{pageid}.json"
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
        page = self.client.fetch_page(title)
        path = self.pages_dir / f"{page['pageid']}.json"
        path.write_text(json.dumps(page, ensure_ascii=False, indent=2), encoding="utf-8")
        return page

    def has_page(self, pageid: int) -> bool:
        return (self.pages_dir / f"{pageid}.json").exists()

    # ---- 검색어 단위 수집 -----------------------------------------------------
    def collect(self, query: str, select_fn: SelectFn, exclude_pageids: set[int] | None = None) -> CollectResult:
        key = normalize_query(query)
        with self._lock:
            cache = self._load_cache()
        if key in cache:
            entry = cache[key]
            pages = [self.get_page(s["pageid"], s["title"]) for s in entry["selected"]]
            return CollectResult(query, True, pages, entry.get("candidates", []), entry.get("rejected", []))

        exclude = exclude_pageids or set()
        cands = [c for c in self.client.search_candidates(query, limit=self.candidates_per_query)
                 if not c["disambiguation"] and c["pageid"] not in exclude]
        chosen = select_fn(cands) if cands else []
        by_pid = {c["pageid"]: c for c in cands}
        chosen = [pid for pid in dict.fromkeys(chosen) if pid in by_pid][: self.max_selected]
        selected = [{"pageid": pid, "title": by_pid[pid]["title"]} for pid in chosen]
        pages = [self.get_page(s["pageid"], s["title"]) for s in selected]
        result = CollectResult(query, False, pages,
                               candidates=[c["title"] for c in cands],
                               rejected=[c["title"] for c in cands if c["pageid"] not in chosen])
        with self._lock:
            cache = self._load_cache()
            cache[key] = {"query": query, "selected": selected, "candidates": result.candidates,
                          "rejected": result.rejected,
                          "collected_at": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")}
            self._save_cache(cache)
        return result
