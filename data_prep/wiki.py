"""한국어 위키백과 클라이언트 — Wikipedia-API(wikipediaapi) 라이브러리 기반.

    import wikipediaapi
    wiki = wikipediaapi.Wikipedia('kdca-rag/0.1 (contact@example.com)', 'ko')
    page = wiki.page('홍역')
    page.exists(), page.title, page.fullurl, page.summary, page.text, page.sections

이 프로젝트가 쓰는 동작과 실제 요청 (라이브러리가 MediaWiki Action API를 대신 호출):
  - page.exists()  → prop=info&redirects=1        존재 여부, 리다이렉트 해석, pageid·fullurl·lastrevid
  - page.text      → prop=extracts&explaintext=1&exsectionformat=wiki   본문 + 섹션 트리
  - wiki.search()  → list=search                   후보 제목 + 검색 스니펫 (프롬프트 2용 요약)

예절 (NF2006): 연락처가 든 User-Agent 필수, 요청은 한 번에 하나씩 + 최소 간격, maxlag=5,
429·5xx는 라이브러리가 Retry-After를 지키며 재시도한다.
"""
from __future__ import annotations

import html
import re
import threading
import time
from datetime import datetime, timezone
from urllib.parse import quote

import wikipediaapi

LANG = "ko"
LICENSE = "CC BY-SA 4.0"
BASE_URL = "https://ko.wikipedia.org/wiki/"
DISAMBIG_MARKERS = ("(동음이의)", "(동음이의어)")
SKIP_SECTIONS = {"각주", "외부 링크", "같이 보기", "참고 문헌", "참고문헌", "출처", "주석", "관련 항목", "더 읽을거리"}
_TAG_RE = re.compile(r"<[^>]+>")

WikiError = wikipediaapi.WikipediaException


def page_url(title: str) -> str:
    return BASE_URL + quote(title.replace(" ", "_"))


def is_disambiguation(title: str) -> bool:
    return any(m in title for m in DISAMBIG_MARKERS)


def clean_snippet(snippet: str) -> str:
    return " ".join(html.unescape(_TAG_RE.sub("", snippet or "")).split())


def flatten_sections(page) -> list[tuple[str, str]]:
    """라이브러리의 섹션 트리를 (섹션명, 본문) 목록으로. 도입부는 '개요', 하위 섹션은 '상위 > 하위'."""
    out = [("개요", (page.summary or "").strip())]

    def walk(sections, prefix=""):
        for s in sections:
            if s.title.strip() in SKIP_SECTIONS:
                continue  # 각주·외부 링크 등은 하위 섹션까지 건너뜀
            name = f"{prefix} > {s.title}" if prefix else s.title
            out.append((name, (s.text or "").strip()))
            walk(s.sections, name)

    walk(page.sections)
    return [(n, t) for n, t in out if t]


class WikiClient:
    def __init__(self, user_agent: str, min_interval: float = 0.5, max_retries: int = 3,
                 timeout: float = 20.0, retry_wait: float = 1.0, transport=None):
        if not user_agent or "@" not in user_agent and "contact" not in user_agent.lower():
            raise ValueError("WIKI_USER_AGENT에 연락처(이메일 등)를 넣어 주세요. 예: kdca-rag/0.1 (contact: me@example.com)")
        kwargs = {"timeout": timeout}
        if transport is not None:  # 테스트에서 httpx.MockTransport 주입
            kwargs["transport"] = transport
        self.wiki = wikipediaapi.Wikipedia(
            user_agent=user_agent, language=LANG, extract_format=wikipediaapi.ExtractFormat.WIKI,
            max_retries=max_retries, retry_wait=retry_wait, extra_api_params={"maxlag": 5}, **kwargs)
        self.min_interval = min_interval
        self._last = 0.0
        self._lock = threading.Lock()

    def _throttle(self) -> None:
        """라이브러리는 호출 간격을 두지 않으므로, 네트워크를 타는 접근 직전에 간격을 맞춘다."""
        with self._lock:
            wait = self.min_interval - (time.monotonic() - self._last)
            if wait > 0:
                time.sleep(wait)
            self._last = time.monotonic()

    # ---- 제목 확인 ------------------------------------------------------------------
    def resolve(self, title: str) -> dict | None:
        """문서가 있으면 {title, pageid, url, redirected, disambiguation}, 없으면 None."""
        page = self.wiki.page(title)
        self._throttle()
        if not page.exists():
            return None
        resolved = page.title
        return {"title": resolved, "pageid": page.pageid, "url": page.fullurl or page_url(resolved),
                "redirected": resolved.replace("_", " ") != title.replace("_", " "),
                "disambiguation": is_disambiguation(resolved), "length": page.length}

    # ---- 후보 검색 -------------------------------------------------------------------
    def search_candidates(self, query: str, limit: int = 5) -> list[dict]:
        self._throttle()
        res = self.wiki.search(query, limit=limit)
        out = []
        for title, page in res.pages.items():
            meta = getattr(page, "search_meta", None)
            out.append({"pageid": page.pageid, "title": title, "url": page_url(title),
                        "summary": clean_snippet(getattr(meta, "snippet", "")),
                        "disambiguation": is_disambiguation(title)})
        return out

    def search_titles(self, query: str, limit: int = 5) -> list[str]:
        return [c["title"] for c in self.search_candidates(query, limit)]

    # ---- 본문 수집 -------------------------------------------------------------------
    def fetch_page(self, title: str) -> dict:
        page = self.wiki.page(title)
        self._throttle()
        if not page.exists():
            raise LookupError(f"위키백과 문서 '{title}'를 찾을 수 없습니다.")
        self._throttle()
        text = page.text  # extracts 호출 (summary·sections도 이 응답으로 채워짐)
        return {
            "doc_id": f"wiki-{page.pageid}", "pageid": page.pageid, "title": page.title,
            "url": page.fullurl or page_url(page.title), "revid": page.lastrevid,
            "summary": (page.summary or "").strip(), "sections": flatten_sections(page), "text": text,
            "source_type": "wiki", "license": LICENSE,
            "fetched_at": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
        }
