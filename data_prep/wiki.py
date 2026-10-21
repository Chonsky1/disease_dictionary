"""한국어 위키백과 문서 존재 여부·길이 확인 (MediaWiki API).

EDA 단계에서는 본문 전체를 받지 않고 존재 여부와 길이만 본다. 본문 수집은
2일차 수집기(F2004)에서 같은 세션 클래스를 재사용한다.
"""
from __future__ import annotations

from .http_client import PoliteSession

API = "https://ko.wikipedia.org/w/api.php"
BATCH = 50  # titles 파라미터 최대 개수


def check_titles(titles: list[str], session: PoliteSession) -> dict[str, dict]:
    """{요청 제목: {exists, resolved_title, redirected, length, pageid}}."""
    results: dict[str, dict] = {}
    for i in range(0, len(titles), BATCH):
        chunk = titles[i:i + BATCH]
        resp = session.get(API, params={
            "action": "query", "format": "json", "formatversion": 2,
            "redirects": 1, "prop": "info", "titles": "|".join(chunk),
        })
        results.update(_parse_query(chunk, resp.json()))
    return results


def _parse_query(requested: list[str], payload: dict) -> dict[str, dict]:
    q = payload.get("query", {})
    normalized = {n["from"]: n["to"] for n in q.get("normalized", [])}
    redirects = {r["from"]: r["to"] for r in q.get("redirects", [])}
    pages = {p["title"]: p for p in q.get("pages", [])}
    out = {}
    for title in requested:
        t = normalized.get(title, title)
        final = redirects.get(t, t)
        page = pages.get(final, {})
        exists = bool(page) and not page.get("missing") and not page.get("invalid")
        out[title] = {
            "exists": exists,
            "resolved_title": final if exists else None,
            "redirected": t in redirects,
            "length": page.get("length") if exists else None,  # 위키텍스트 바이트 수
            "pageid": page.get("pageid") if exists else None,
        }
    return out


def search_titles(query: str, session: PoliteSession, limit: int = 5) -> list[str]:
    """문서가 없을 때 대체 제목 후보 찾기."""
    resp = session.get(API, params={"action": "query", "format": "json", "formatversion": 2,
                                    "list": "search", "srsearch": query, "srlimit": limit})
    return [h["title"] for h in resp.json().get("query", {}).get("search", [])]
