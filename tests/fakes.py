"""네트워크·API 키 없이 파이프라인을 끝까지 돌리기 위한 가짜 구성요소.

- RuleChatModel: 프롬프트 역할 문구를 보고 정해진 형식의 답을 주는 가짜 LLM
- FakeMediaWiki: ko.wikipedia.org/w/api.php 흉내. httpx.MockTransport로 진짜 wikipediaapi 라이브러리에 꽂는다.
"""
from __future__ import annotations

import json
import re
from collections import Counter
from typing import Any
from urllib.parse import parse_qs, urlparse

import httpx
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult

from data_prep.wiki import WikiClient


class RuleChatModel(BaseChatModel):
    calls: list = []
    answer_text: str = "홍역은 제2급 감염병이며 24시간 이내에 신고합니다 [근거 1]. 홍역 바이러스가 공기로 전파됩니다 [근거 2]."

    @property
    def _llm_type(self) -> str:
        return "rule-fake"

    def _generate(self, messages, stop=None, run_manager=None, **kwargs: Any) -> ChatResult:
        system, human = messages[0].content, messages[-1].content
        if "검색어를 만든다" in system:
            kind, out = "query", json.dumps({"queries": ["홍역 바이러스", "MMR 백신"]}, ensure_ascii=False)
        elif "검토자" in system:
            kind, out = "select", '```json\n{"selected": ["c1"]}\n```'
        elif "여행 안내원" in system:      # 여행 전 안내: 공공데이터 API 근거와 첫 메타데이터 근거를 인용
            api = re.findall(r"\[근거 (\d+)\] \(출처 유형: 공공데이터 API", human)
            n = api[0] if api else "1"
            name = (re.findall(r"\[감염병: ([^|\]]+?) \|", human) or ["감염병"])[0]
            kind, out = "travel", (f"### 핵심 답변\n{name}에 주의해야 합니다 [근거 {n}].\n\n### {name}\n"
                                   f"- 이 나라가 검역관리지역으로 지정되어 있습니다 [근거 {n}]\n"
                                   "- 신고 기준은 항목 정보에 있습니다 [근거 1]")
        elif "안내원" in system:      # 문서에 없는 것(가격·특정 지역 환자 수)은 확인되지 않는다고 답함
            unknown = any(k in human.split("[근거]")[0] for k in ("가격", "약가", "확진자 수"))
            kind, out = "answer", ("제공된 문서에서 확인되지 않습니다." if unknown else self.answer_text)
        elif "걸러 주는" in system:
            ids = re.findall(r"id: (inf_\d{4})", human)[:2]
            kind, out = "condition", json.dumps({"matches": [{"id": i, "reason": "조건 충족"} for i in ids] +
                                                  [{"id": "inf_9999", "reason": "후보에 없던 ID"}]})
        else:
            kind, out = "other", "?"
        self.calls.append(kind)
        return ChatResult(generations=[ChatGeneration(message=AIMessage(content=out))])


LONG = "홍역은 홍역 바이러스에 의한 급성 감염병이다. " * 60
PAGES = {
    "홍역": (100, f"{LONG}\n\n== 증상 ==\n발열과 발진이 나타난다.\n\n=== 합병증 ===\n폐렴이 생길 수 있다.\n\n"
                  "== 각주 ==\n[1] 출처\n\n== 외부 링크 ==\n링크"),
    "홍역 바이러스": (200, "홍역 바이러스는 파라믹소바이러스과에 속한다.\n\n== 전파 ==\n공기로 전파된다."),
    "MMR 백신": (300, "MMR 백신은 홍역·유행성이하선염·풍진을 예방한다."),
    "홍역 (영화)": (400, "영화 문서"),
    "홍역 (동음이의)": (500, "홍역은 다음을 가리킨다."),
}
REDIRECTS = {"Measles": "홍역", "중증급성 호흡기 증후군": "홍역"}
SEARCH = {"홍역 바이러스": ["홍역 바이러스", "홍역 (영화)", "홍역 (동음이의)"], "MMR 백신": ["MMR 백신", "홍역"],
          "수두": ["홍역"]}


class FakeMediaWiki:
    """요청을 기록하고 MediaWiki Action API 형식(formatversion 1, 라이브러리 기본값)으로 응답한다."""

    def __init__(self, fail: bool = False):
        self.requests: list[dict] = []
        self.fail = fail

    def counts(self) -> Counter:
        return Counter(r.get("list") or r.get("prop") for r in self.requests)

    def __call__(self, request: httpx.Request) -> httpx.Response:
        q = {k: v[0] for k, v in parse_qs(urlparse(str(request.url)).query).items()}
        q["_ua"] = request.headers.get("User-Agent", "")
        self.requests.append(q)
        if self.fail:
            return httpx.Response(503, text="busy")
        if q.get("list") == "search":
            hits = SEARCH.get(q["srsearch"], [])[: int(q.get("srlimit", 10))]
            return httpx.Response(200, json={"query": {"searchinfo": {"totalhits": len(hits)}, "search": [
                {"ns": 0, "title": t, "pageid": PAGES[t][0], "snippet": f"<span class=\"searchmatch\">{t}</span> 요약",
                 "size": 1, "wordcount": 1} for t in hits]}})
        title = q.get("titles", "")
        redirects = [{"from": title, "to": REDIRECTS[title]}] if title in REDIRECTS else []
        title = REDIRECTS.get(title, title)
        if title not in PAGES:
            return httpx.Response(200, json={"query": {"pages": {"-1": {"ns": 0, "title": title, "missing": ""}}}})
        pid, body = PAGES[title]
        page = {"pageid": pid, "ns": 0, "title": title}
        if q.get("prop") == "info":
            page.update(fullurl=f"https://ko.wikipedia.org/wiki/{title}", lastrevid=pid * 10, length=len(body))
        elif q.get("prop") == "extracts":
            page["extract"] = body
        return httpx.Response(200, json={"query": {"redirects": redirects, "pages": {str(pid): page}}})


def fake_wiki_client(server: FakeMediaWiki | None = None) -> tuple[WikiClient, FakeMediaWiki]:
    server = server or FakeMediaWiki()
    client = WikiClient("kdca-rag-test/0.1 (contact: test@example.com)", min_interval=0, max_retries=1,
                        retry_wait=0.01, transport=httpx.MockTransport(server))
    return client, server
