"""경로와 환경변수. 키 값은 절대 출력하지 않는다 (NF2003, NF2004)."""
from __future__ import annotations

import os
from pathlib import Path

try:  # python-dotenv는 필수 라이브러리. 없으면 OS 환경변수만 사용.
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover
    load_dotenv = None

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "config"
DATA = ROOT / "data"
SEED = DATA / "seed"
RAW = DATA / "raw"
RAW_API = RAW / "api"
RAW_CSV = RAW / "csv"
PROCESSED = DATA / "processed"
REPORTS = ROOT / "reports"

if load_dotenv is not None:
    load_dotenv(ROOT / ".env")


class MissingEnvError(RuntimeError):
    """필요한 환경변수가 없을 때. 메시지에는 변수 이름만 담는다."""


def env(name: str, required: bool = False, default: str | None = None) -> str | None:
    value = os.getenv(name, default)
    if required and not value:
        raise MissingEnvError(f"환경변수 {name} 이(가) 설정되지 않았습니다. .env.example을 참고하세요.")
    return value


def ensure_dirs() -> None:
    for p in (RAW_API, RAW_CSV, PROCESSED, REPORTS, WIKI_PAGES, CACHE, LOGS):
        p.mkdir(parents=True, exist_ok=True)


# --- 단계 1 RAG 경로 -----------------------------------------------------------
DISEASES_CSV = SEED / "infectious_diseases.csv"
WIKI_RAW = RAW / "wiki"
WIKI_PAGES = WIKI_RAW / "pages"            # 수집한 위키 본문 원본 (pageid.json)
WIKI_TITLES = PROCESSED / "wiki_titles.json"
CACHE = DATA / "cache"
QUERY_CACHE = CACHE / "query_cache.json"   # NF2002 수집 캐시
LOGS = ROOT / "logs"


def load_rag_settings(path: Path | None = None, **overrides) -> dict:
    """config/rag_settings.json + 호출 시 덮어쓰기(실험용)."""
    import json

    cfg = json.loads((path or CONFIG / "rag_settings.json").read_text(encoding="utf-8"))
    cfg = {k: v for k, v in cfg.items() if not k.startswith("_")}
    cfg.update({k: v for k, v in overrides.items() if v is not None})
    if cfg["chunk_overlap"] >= cfg["chunk_size"]:
        raise ValueError("chunk_overlap은 chunk_size보다 작아야 합니다.")
    if cfg["search_type"] not in ("similarity", "mmr"):
        raise ValueError("search_type은 similarity 또는 mmr 이어야 합니다.")
    cfg["chroma_dir"] = str((ROOT / cfg["chroma_dir"]).resolve())
    return cfg
