"""OpenAI(또는 GMS 등 호환 프록시) 연결 점검. 색인·화면 실행 전에 한 번 돌려 본다.

python -m scripts.check_openai

채팅 모델과 임베딩 모델을 각각 아주 작게 호출해, 어느 쪽이 왜 실패하는지 알려 준다.
키 값은 출력하지 않는다 (NF2004).
"""
from __future__ import annotations

import os
import sys
from urllib.parse import urlparse

from data_prep import settings as paths


def _short(exc: Exception) -> str:
    msg = str(exc).replace(os.getenv("OPENAI_API_KEY", "") or "\0", "***")
    return f"{type(exc).__name__}: {msg[:300]}"


def main() -> int:
    settings = paths.load_rag_settings()
    if not os.getenv("OPENAI_API_KEY"):
        print("[x] OPENAI_API_KEY가 비어 있습니다. .env를 확인하세요.")
        return 1
    base = os.getenv("OPENAI_BASE_URL") or "(기본) https://api.openai.com/v1"
    print(f"[i] base_url: {base}")
    if os.getenv("OPENAI_BASE_URL"):
        path = urlparse(os.environ["OPENAI_BASE_URL"]).path.rstrip("/")
        if not path.endswith("/v1"):
            print("[!] base_url이 /v1로 끝나지 않습니다. '/chat/completions'나 '/embeddings'까지 넣지 말고 "
                  "'…/v1'까지만 적어야 합니다.")
    print(f"[i] chat_model: {settings['chat_model']} / embedding_model: {settings['embedding_model']}")

    ok = True
    try:
        from rag.llm import make_chat_model
        out = make_chat_model(settings).invoke("한 단어로만 답하세요: 하늘 색은?")
        print(f"[v] 채팅 모델 정상: {str(out.content)[:30]!r}")
    except Exception as exc:  # noqa: BLE001
        ok = False
        print(f"[x] 채팅 모델 실패 → {_short(exc)}")
        print("    · 404/model_not_found면 config/rag_settings.json의 chat_model을 제공되는 모델 이름으로 바꾸세요.")

    try:
        from vectorstore.store import make_embeddings
        vec = make_embeddings(settings).embed_documents(["홍역은 제2급 감염병이다.", "수두"])
        print(f"[v] 임베딩 정상: {len(vec)}건, {len(vec[0])}차원")
    except Exception as exc:  # noqa: BLE001
        ok = False
        print(f"[x] 임베딩 실패 → {_short(exc)}")
        print("    · \"Unknown parameter: 'input'\"이 계속되면 rag_settings.json의 embedding_raw_text를 true로 두고 다시 시도")
        print("    · 그래도 안 되면 프록시가 임베딩 모델을 지원하는지, embedding_model 이름이 맞는지 확인하세요.")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
