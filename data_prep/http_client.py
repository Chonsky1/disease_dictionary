"""외부 API 호출 예절 (NF2006): 호출 간격, 재시도, User-Agent, 키 마스킹."""
from __future__ import annotations

import re
import time

import requests

RETRY_STATUS = {429, 500, 502, 503, 504}
_KEY_PATTERN = re.compile(r"(serviceKey|ServiceKey|crtfc_key|api_key)=[^&\s]+")


def mask_secrets(text: str) -> str:
    """URL·오류 메시지에 섞인 인증키를 가린다."""
    return _KEY_PATTERN.sub(r"\1=***", text)


class PoliteSession:
    def __init__(
        self,
        user_agent: str,
        min_interval: float = 1.0,
        max_retries: int = 3,
        backoff: float = 2.0,
        timeout: float = 20.0,
        session: requests.Session | None = None,
        sleep=time.sleep,
    ) -> None:
        if not user_agent:
            raise ValueError("User-Agent가 필요합니다 (연락처 포함 권장).")
        self.session = session or requests.Session()
        self.session.headers["User-Agent"] = user_agent
        self.min_interval = min_interval
        self.max_retries = max_retries
        self.backoff = backoff
        self.timeout = timeout
        self._sleep = sleep
        self._last_call = 0.0

    def _wait_turn(self) -> None:
        elapsed = time.monotonic() - self._last_call
        if elapsed < self.min_interval:
            self._sleep(self.min_interval - elapsed)
        self._last_call = time.monotonic()

    def get(self, url: str, params: dict | None = None) -> requests.Response:
        last_error: str = ""
        for attempt in range(self.max_retries + 1):
            self._wait_turn()
            try:
                resp = self.session.get(url, params=params, timeout=self.timeout)
            except (requests.ConnectionError, requests.Timeout) as exc:
                last_error = f"{type(exc).__name__}"
            else:
                if resp.status_code not in RETRY_STATUS:
                    if resp.status_code >= 400:
                        raise requests.HTTPError(
                            f"HTTP {resp.status_code}: {mask_secrets(resp.url)}", response=resp
                        )
                    return resp
                last_error = f"HTTP {resp.status_code}"
            if attempt < self.max_retries:
                self._sleep(self.backoff ** attempt)
        raise requests.ConnectionError(f"재시도 {self.max_retries}회 후 실패: {last_error} ({mask_secrets(url)})")
