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
    for p in (RAW_API, RAW_CSV, PROCESSED, REPORTS):
        p.mkdir(parents=True, exist_ok=True)
