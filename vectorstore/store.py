"""두 컬렉션을 다루는 Vector Store (F2002, F2005, F2006, F2014).

- disease_meta : 감염병 1건 = 1청크, 청크 ID = disease_id (재색인해도 덮어쓰기만 일어남)
- disease_docs : 외부 문서 청크, 청크 ID = "disease_id:doc_id:순번" (같은 문서를 여러 감염병이
                 수집해도 서로 덮어쓰지 않음). 출처 종류(source_type)가 다른 문서를 함께 담는다 (F2016):
                 wiki(위키백과, 글자 수 청킹) · public_api / public_stats(공공데이터 레코드, 섹션 = 청크)
"""
from __future__ import annotations

import hashlib
import os

from langchain_chroma import Chroma
from langchain_core.documents import Document
from langchain_core.embeddings import Embeddings
from langchain_text_splitters import RecursiveCharacterTextSplitter

from data_prep.wiki import SKIP_SECTIONS

SEPARATORS = ["\n\n", "\n", ". ", "다. ", " ", ""]


def make_embeddings(settings: dict) -> Embeddings:
    """OpenAI 호환 프록시(SSAFY GMS 등)를 쓰면 토큰 배열 대신 원문 텍스트를 보낸다.

    OpenAIEmbeddings는 기본적으로 tiktoken으로 자른 '토큰 ID 배열'을 input에 넣는데, 많은 프록시가
    이를 받지 못한다 (예: GMS의 400 "Unknown parameter: 'input'"). LangChain 문서가 권하는 대로
    check_embedding_ctx_length=False로 원문을 보내고, 응답은 float 형식으로 받는다.
    우리 청크는 1,000자 안팎이라 모델 입력 한도(8,191토큰)를 넘지 않으므로 길이 검사가 필요 없다.
    """
    from data_prep import settings as paths
    if paths.OFFLINE:      # 오프라인 모의 실행 (scripts/offline_env.py)
        from langchain_core.embeddings import DeterministicFakeEmbedding
        return DeterministicFakeEmbedding(size=32)
    from langchain_openai import OpenAIEmbeddings

    kwargs = {"model": settings["embedding_model"]}
    base_url = os.getenv("OPENAI_BASE_URL")
    raw_text = settings.get("embedding_raw_text", "auto")
    if base_url:
        kwargs["base_url"] = base_url
    if raw_text is True or (raw_text == "auto" and base_url):
        kwargs["check_embedding_ctx_length"] = False
        kwargs["model_kwargs"] = {"encoding_format": "float"}
    return OpenAIEmbeddings(**kwargs)


def _chroma_client(path: str):
    import chromadb
    from chromadb.config import Settings

    os.makedirs(path, exist_ok=True)
    return chromadb.PersistentClient(path=path, settings=Settings(anonymized_telemetry=False))


class DiseaseStore:
    def __init__(self, settings: dict, embeddings: Embeddings, client=None):
        self.settings = settings
        self.client = client or _chroma_client(settings["chroma_dir"])
        self.meta = Chroma(client=self.client, collection_name=settings["meta_collection"],
                           embedding_function=embeddings)
        self.docs = Chroma(client=self.client, collection_name=settings["docs_collection"],
                           embedding_function=embeddings)

    # ---- 항목 메타데이터 (RAG 1) ----------------------------------------------
    def index_meta(self, texts: list[str], metadatas: list[dict]) -> int:
        ids = [m["disease_id"] for m in metadatas]
        if len(set(ids)) != len(ids):
            raise ValueError("disease_id가 중복됩니다.")
        self.meta.add_texts(texts=texts, metadatas=metadatas, ids=ids)  # Chroma upsert → 멱등
        return self.meta_count()

    def meta_count(self) -> int:
        return self.meta._collection.count()

    def get_meta(self, disease_id: str) -> Document | None:
        got = self.meta._collection.get(ids=[disease_id], include=["documents", "metadatas"])
        if not got["ids"]:
            return None
        return Document(id=disease_id, page_content=got["documents"][0], metadata=got["metadatas"][0])

    def search_meta(self, query: str, k: int, where: dict | None = None) -> list[Document]:
        return self.meta.similarity_search(query, k=k, filter=where or None)

    # ---- 외부 문서 (RAG 2) ------------------------------------------------------
    def chunk_page(self, disease_id: str, disease_name: str, page: dict, query: str) -> list[Document]:
        """섹션별로 나눈 뒤 청크 크기·중복 길이를 적용한다. 청크 앞에 짧은 맥락 머리말을 붙인다.

        page["chunking"] == "record"(공공데이터 레코드 문서)면 글자 수로 자르지 않고 섹션 하나를 청크 하나로 둔다
        (감염병·기준일과 값이 다른 청크로 갈라지지 않게. data_prep/public_docs.py 참고).
        """
        splitter = RecursiveCharacterTextSplitter(
            chunk_size=self.settings["chunk_size"], chunk_overlap=self.settings["chunk_overlap"],
            separators=SEPARATORS)
        docs, seq = [], 0
        sections = page.get("sections") or [("본문", page.get("text", ""))]
        for section, body in sections:  # 라이브러리 섹션 트리를 펼친 목록 (data_prep.wiki.flatten_sections)
            if section.split(" > ")[0] in SKIP_SECTIONS or not body.strip():
                continue
            pieces = [body.strip()] if page.get("chunking") == "record" else splitter.split_text(body)
            for piece in pieces:
                header = f"[감염병: {disease_name} | 문서: {page['title']} | 섹션: {section}]"
                docs.append(Document(
                    id=f"{disease_id}:{page['doc_id']}:{seq:04d}",
                    page_content=f"{header}\n{piece}",
                    metadata={
                        "disease_id": disease_id, "disease_name": disease_name,
                        "doc_id": page["doc_id"], "title": page["title"], "section": section,
                        "source_type": page.get("source_type", "wiki"),
                        "source_label": page.get("source_label", "위키백과"),
                        "source_url": page["url"], "license": page.get("license", ""),
                        "query": query, "collected_at": page.get("fetched_at", ""), "seq": seq,
                    }))
                seq += 1
        return docs

    def index_page(self, disease_id: str, disease_name: str, page: dict, query: str) -> int:
        """같은 (감염병, 문서)의 기존 청크를 지우고 다시 넣는다 (청크 설정을 바꿔도 낡은 청크가 안 남음)."""
        self.docs._collection.delete(where={"$and": [{"disease_id": disease_id}, {"doc_id": page["doc_id"]}]})
        chunks = self.chunk_page(disease_id, disease_name, page, query)
        if chunks:
            self.docs.add_documents(chunks, ids=[c.id for c in chunks])
        return len(chunks)

    def is_indexed(self, disease_id: str, doc_id: str) -> bool:
        got = self.docs._collection.get(where={"$and": [{"disease_id": disease_id}, {"doc_id": doc_id}]},
                                        limit=1, include=[])
        return bool(got["ids"])

    def docs_count(self, disease_id: str | None = None, source_type: str | None = None) -> int:
        if disease_id is None and source_type is None:
            return self.docs._collection.count()
        conds = ([{"disease_id": disease_id}] if disease_id else []) + \
                ([{"source_type": source_type}] if source_type else [])
        where = conds[0] if len(conds) == 1 else {"$and": conds}
        return len(self.docs._collection.get(where=where, include=[])["ids"])

    def count_by_type(self) -> dict[str, int]:
        """출처 종류별 청크 수 (F2016 확인용)."""
        out: dict[str, int] = {}
        for m in self.docs._collection.get(include=["metadatas"])["metadatas"]:
            t = m.get("source_type", "wiki")
            out[t] = out.get(t, 0) + 1
        return out

    def public_doc_pairs(self) -> set[tuple[str, str]]:
        got = self.docs._collection.get(where={"source_type": {"$in": ["public_api", "public_stats"]}},
                                        include=["metadatas"])
        return {(m["disease_id"], m["doc_id"]) for m in got["metadatas"]}

    def get_docs(self, disease_id: str, source_type: str) -> list[Document]:
        """이 감염병의 특정 출처 종류 청크 전부 (검색 없이 ID 순)."""
        got = self.docs._collection.get(where={"$and": [{"disease_id": disease_id}, {"source_type": source_type}]},
                                        include=["documents", "metadatas"])
        return sorted((Document(id=i, page_content=t, metadata=m)
                       for i, t, m in zip(got["ids"], got["documents"], got["metadatas"])), key=lambda d: d.id)

    def delete_doc(self, disease_id: str, doc_id: str) -> None:
        self.docs._collection.delete(where={"$and": [{"disease_id": disease_id}, {"doc_id": doc_id}]})

    def list_docs(self, disease_id: str) -> list[dict]:
        """사이드바용: 이 감염병으로 색인된 문서 목록 (중복 제거)."""
        got = self.docs._collection.get(where={"disease_id": disease_id}, include=["metadatas"])
        seen: dict[str, dict] = {}
        for m in got["metadatas"]:
            seen.setdefault(m["doc_id"], {"title": m["title"], "url": m["source_url"], "chunks": 0,
                                          "source_type": m.get("source_type", "wiki"),
                                          "source_label": m.get("source_label", "위키백과")})
            seen[m["doc_id"]]["chunks"] += 1
        return list(seen.values())

    def search_docs(self, query: str, disease_id: str, k: int | None = None,
                    search_type: str | None = None) -> list[Document]:
        """선택한 감염병 범위 안에서만 검색 (disease_id 필터가 빠지면 다른 감염병 문서가 섞임)."""
        k = k or self.settings["top_k"]
        search_type = search_type or self.settings["search_type"]
        where = {"disease_id": disease_id}
        if search_type == "mmr":
            return self.docs.max_marginal_relevance_search(
                query, k=k, fetch_k=self.settings["mmr_fetch_k"],
                lambda_mult=self.settings["mmr_lambda"], filter=where)
        return self.docs.similarity_search(query, k=k, filter=where)


def text_hash(text: str) -> str:
    body = text.split("\n", 1)[-1]  # 머리말 제외 본문 기준
    return hashlib.sha1(" ".join(body.split()).encode("utf-8")).hexdigest()
