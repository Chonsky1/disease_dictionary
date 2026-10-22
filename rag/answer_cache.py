"""같은 감염병·같은 질문의 답을 저장해 두고 바로 돌려준다 (rag_settings.json의 answer_cache).

키 = 감염병 ID + 정규화한 질문 + 답변 모델 + 프롬프트 버전. 프롬프트나 모델을 바꾸면 자동으로 새로 만든다.
색인이 바뀐 뒤에도 예전 답을 주지 않도록 max_age_days(기본 7일)가 지나면 버린다.
"""
from __future__ import annotations

import hashlib
import json
import re
import threading
import time
from pathlib import Path


def normalize_question(q: str) -> str:
    return re.sub(r"[\s?!.,~]+", " ", q).strip().lower()


class AnswerCache:
    def __init__(self, path: Path, max_age_days: float = 7, max_items: int = 2000):
        self.path = Path(path)
        self.max_age = max_age_days * 86400
        self.max_items = max_items
        self._lock = threading.Lock()

    @staticmethod
    def key(disease_id: str, question: str, model: str, version: str) -> str:
        raw = "|".join([disease_id, normalize_question(question), model, version])
        return hashlib.sha1(raw.encode("utf-8")).hexdigest()

    def _load(self) -> dict:
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except (FileNotFoundError, ValueError):
            return {}

    def get(self, key: str) -> dict | None:
        with self._lock:
            item = self._load().get(key)
        if not item or time.time() - item.get("saved", 0) > self.max_age:
            return None
        return item["value"]

    def put(self, key: str, value: dict) -> None:
        with self._lock:
            data = self._load()
            data[key] = {"saved": time.time(), "value": value}
            if len(data) > self.max_items:   # 오래된 것부터 버림
                for k in sorted(data, key=lambda k: data[k]["saved"])[: len(data) - self.max_items]:
                    data.pop(k)
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
            tmp.replace(self.path)
