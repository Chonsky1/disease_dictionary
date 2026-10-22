"""모듈을 엮는 조립 함수. 화면(ui)과 스크립트가 같은 조립을 쓴다."""
from __future__ import annotations

import logging
import threading
from dataclasses import dataclass

from data_prep import settings as paths
from data_prep.collector import WikiCollector
from data_prep.diseases import Disease, load_diseases
from data_prep.wiki import WikiClient
from data_prep.wiki_titles import load_titles
from rag.graph import RagServices, build_graph
from vectorstore.store import DiseaseStore

DISCLAIMER = ("이 서비스는 공개된 공공자료와 백과사전을 근거로 한 일반 정보이며, 의사·약사 등 전문가의 진단이나 "
              "조언을 대신하지 않습니다. 감염병이 의심되면 질병관리청 콜센터(1339)나 가까운 의료기관에 문의하세요.")


def setup_logging() -> None:
    """오류 추적 정보는 화면이 아니라 logs/app.log에만 남긴다 (NF2004)."""
    paths.LOGS.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("kdca_rag")
    if not logger.handlers:
        h = logging.FileHandler(paths.LOGS / "app.log", encoding="utf-8")
        h.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        logger.addHandler(h)
        logger.setLevel(logging.INFO)


def make_wiki_client(settings: dict) -> WikiClient:
    ua = paths.env("WIKI_USER_AGENT", required=True)
    return WikiClient(ua, min_interval=settings["wiki_min_interval_sec"], max_retries=settings["wiki_max_retries"])


@dataclass
class Runtime:
    settings: dict
    diseases: list[Disease]
    by_id: dict[str, Disease]
    store: DiseaseStore
    services: RagServices
    graph: object


def build_runtime(settings: dict | None = None, llm=None, embeddings=None, wiki_client=None,
                  collect: bool = True) -> Runtime:
    """llm·embeddings·wiki_client를 넘기면 그것을 쓰고(테스트), 아니면 설정대로 만든다."""
    settings = settings or paths.load_rag_settings()
    if embeddings is None:
        from vectorstore.store import make_embeddings
        embeddings = make_embeddings(settings)
    if llm is None:
        from rag.llm import make_chat_model
        llm = make_chat_model(settings)

    diseases = load_diseases(paths.DISEASES_CSV)
    by_id = {d.disease_id: d for d in diseases}
    store = DiseaseStore(settings, embeddings)
    collector = None
    if collect:
        collector = WikiCollector(wiki_client or make_wiki_client(settings), paths.WIKI_PAGES,
                                  paths.QUERY_CACHE, settings["candidates_per_query"],
                                  settings["max_selected_per_query"])
    aux = None
    if settings.get("aux_model") and settings["aux_model"] != settings["chat_model"]:
        from rag.llm import make_chat_model
        aux = make_chat_model({**settings, "chat_model": settings["aux_model"]})
    cache = None
    if settings.get("answer_cache", True) and collect:
        from rag.answer_cache import AnswerCache
        cache = AnswerCache(paths.CACHE / "answer_cache.json")
    svc = RagServices(llm=llm, store=store, collector=collector, diseases=by_id,
                      wiki_titles=load_titles(paths.WIKI_TITLES), settings=settings, aux_llm=aux, answer_cache=cache)
    if settings.get("warmup", True) and collect:
        warmup(store)
    return Runtime(settings, diseases, by_id, store, svc, build_graph(svc))


def warmup(store) -> threading.Thread:
    """임베딩 연결을 미리 열어 둔다 (실측: 첫 근거 검색 2.4~2.7초 → 이후 0.2~0.3초). 질문을 기다리게 하지 않게 배경에서."""
    def job():
        try:
            store.docs.embeddings.embed_query("감염병")
        except Exception:  # noqa: BLE001 - 실패해도 첫 질문이 조금 느릴 뿐
            logging.getLogger("kdca_rag").debug("임베딩 예열 실패", exc_info=True)
    t = threading.Thread(target=job, name="warmup", daemon=True)
    t.start()
    return t
