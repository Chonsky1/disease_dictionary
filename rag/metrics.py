"""답변 한 번에 걸린 시간·호출 수 기록 (화면 표시, scripts/bench_chat.py 측정에 사용).

stages : 단계별 실제 경과 시간(초). 병렬로 돈 단계는 바깥 단계(collect)의 벽시계 시간으로 본다.
calls  : 종류별 호출 수 (llm.queries / llm.select / llm.answer / wiki.search / wiki.fetch / embed.index / embed.query)
llm_s  : LLM 호출에 쓴 시간 합 (병렬 호출은 겹쳐서 더해질 수 있음)
first_token_s : 질문부터 답변 첫 글자가 나오기까지 (스트리밍일 때 체감 대기 시간)
"""
from __future__ import annotations

import threading
import time
from collections import Counter
from contextlib import contextmanager


class Meter:
    def __init__(self) -> None:
        self.t0 = time.perf_counter()
        self.stages: dict[str, float] = {}
        self.calls: Counter = Counter()
        self.llm_s = 0.0
        self.first_token_s: float | None = None
        self.notes: list[str] = []
        self._lock = threading.Lock()

    @contextmanager
    def stage(self, name: str):
        start = time.perf_counter()
        try:
            yield
        finally:
            with self._lock:
                self.stages[name] = self.stages.get(name, 0.0) + time.perf_counter() - start

    @contextmanager
    def llm(self, purpose: str):
        start = time.perf_counter()
        try:
            yield
        finally:
            with self._lock:
                self.calls[f"llm.{purpose}"] += 1
                self.llm_s += time.perf_counter() - start

    def count(self, kind: str, n: int = 1) -> None:
        with self._lock:
            self.calls[kind] += n

    def first_token(self) -> None:
        if self.first_token_s is None:
            self.first_token_s = time.perf_counter() - self.t0

    def note(self, text: str) -> None:
        self.notes.append(text)

    def summary(self) -> dict:
        total = time.perf_counter() - self.t0
        return {"total_s": round(total, 3),
                "first_token_s": round(self.first_token_s if self.first_token_s is not None else total, 3),
                "stages": {k: round(v, 3) for k, v in self.stages.items()},
                "calls": dict(self.calls), "llm_calls": sum(v for k, v in self.calls.items() if k.startswith("llm.")),
                "llm_s": round(self.llm_s, 3), "notes": list(self.notes)}


def describe(t: dict) -> str:
    """화면용 한 줄: '3.2초 · 첫 글자 1.1초 · LLM 2회 (검색어 1.0 · 수집 0.8 · 답변 1.3)'."""
    if not t:
        return ""
    if t.get("cache_hit"):
        return f"{t['total_s']:.2f}초 · 같은 질문의 저장된 답변"
    names = {"queries": "검색어", "collect": "수집", "search": "근거 검색", "generate": "답변"}
    parts = " · ".join(f"{names.get(k, k)} {v:.1f}" for k, v in t.get("stages", {}).items() if k in names)
    head = f"{t['total_s']:.1f}초"
    if t.get("first_token_s") is not None and t["first_token_s"] < t["total_s"] - 0.05:
        head += f" · 첫 글자 {t['first_token_s']:.1f}초"
    return f"{head} · LLM {t.get('llm_calls', 0)}회" + (f" ({parts})" if parts else "")
