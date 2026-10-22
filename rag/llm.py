"""채팅 모델 생성. 키는 환경변수에서만 읽는다 (NF2003)."""
from __future__ import annotations

import os

from langchain_core.language_models import BaseChatModel


def make_chat_model(settings: dict) -> BaseChatModel:
    from langchain_openai import ChatOpenAI

    kwargs = {"model": settings["chat_model"], "temperature": settings.get("temperature", 0), "timeout": 60,
              "max_retries": 2}
    if os.getenv("OPENAI_BASE_URL"):  # SSAFY GMS 등 OpenAI 호환 프록시
        kwargs["base_url"] = os.environ["OPENAI_BASE_URL"]
    return ChatOpenAI(**kwargs)


def missing_keys() -> list[str]:
    """화면에서 안내할 누락 환경변수 이름 목록 (값은 절대 반환하지 않음)."""
    required = ["OPENAI_API_KEY", "WIKI_USER_AGENT"]
    return [k for k in required if not os.getenv(k)]
