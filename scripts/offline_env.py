"""키·네트워크 없이 검증 도구를 돌려 보기 위한 임시 작업 환경 (가짜 모델·가짜 위키·합성 데이터).

- 결과는 '도구가 동작하는지' 확인용이며 실제 서비스 품질의 증빙이 아니다.
  검증 결과에는 반드시 실제 모델(.env)로 실행한 것을 쓴다.
- 두 가지 방식
  1) 환경변수: KDCA_OFFLINE_DEMO=1 + KDCA_DATA_DIR=<임시 폴더> 로 실행하면 모델·임베딩·위키 생성 함수가 가짜를 돌려준다
     (data_prep/settings.py OFFLINE). 스크립트와 Streamlit 앱을 '실제와 같은 명령'으로 돌릴 수 있다.
     먼저 `python -m scripts.offline_env --prepare`로 그 폴더에 합성 데이터를 만든다.
     KDCA_OFFLINE_FAIL=1이면 가짜 모델이 오류를 낸다 (오류 화면 확인용).
  2) 함수: offline_runtime() - 한 프로세스 안에서 (scripts.compare_rag --offline, 테스트)
"""
from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path

from data_prep import settings as paths

ENV_FLAG = "KDCA_OFFLINE_DEMO"
ENV_ROOT = "KDCA_OFFLINE_ROOT"


def prepare_data(root: Path) -> Path:
    """합성 검역관리지역·연령 통계 파일 (tests/fixtures 기반)."""
    from tests.age_fixtures import build_sample, make_stats_dir
    from tests.test_ui import write_sample_watch

    processed = root / "processed"
    processed.mkdir(parents=True, exist_ok=True)
    if not (processed / "country_watch.json").exists():
        write_sample_watch(processed / "country_watch.json")
    if not (processed / "age_stats.json").exists():
        build_sample(processed / "age_stats.json", make_stats_dir(root / "stats_in"))
    return processed


def offline_runtime(root: Path | None = None, indexed: bool = True, **overrides):
    """(Runtime, cleanup). root를 주면 그 폴더를 쓰고 지우지 않는다."""
    from langchain_core.embeddings import DeterministicFakeEmbedding

    from data_prep.collector import WikiCollector
    from data_prep.diseases import build_index_text, build_metadata, load_diseases
    from data_prep.public_docs import index_public_docs
    from rag.graph import RagServices, build_graph
    from service.age import load_age_stats
    from service.countries import load_watch
    from service.runtime import Runtime
    from tests.fakes import RuleChatModel, fake_wiki_client
    from vectorstore.store import DiseaseStore

    own = root is None
    root = Path(root or tempfile.mkdtemp(prefix="kdca_offline_"))
    processed = prepare_data(root)
    old_processed = paths.PROCESSED
    paths.PROCESSED = processed            # 스크립트·화면이 호출 시점에 읽는 경로
    settings = {**paths.load_rag_settings(), "chroma_dir": str(root / "chroma"), "warmup": False,
                "answer_cache": False, **overrides}
    store = DiseaseStore(settings, DeterministicFakeEmbedding(size=32))
    diseases = load_diseases(paths.DISEASES_CSV)
    if indexed and store.meta_count() == 0:
        store.index_meta([build_index_text(d) for d in diseases], [build_metadata(d) for d in diseases])
        index_public_docs(store, diseases, load_watch(processed / "country_watch.json"),
                          load_age_stats(processed / "age_stats.json"))
    by_id = {d.disease_id: d for d in diseases}
    measles = next(d for d in diseases if d.display_name == "홍역")
    llm = RuleChatModel()
    llm.calls = []
    collector = WikiCollector(fake_wiki_client()[0], root / "pages", root / "query_cache.json")
    svc = RagServices(llm, store, collector, by_id, {measles.disease_id: {"title": "홍역", "pageid": 100}}, settings)
    rt = Runtime(settings, diseases, by_id, store, svc, build_graph(svc))

    def cleanup() -> None:
        paths.PROCESSED = old_processed
        if own:
            shutil.rmtree(root, ignore_errors=True)
    return rt, cleanup


def offline_chat_model():
    from tests.fakes import RuleChatModel
    if os.getenv("KDCA_OFFLINE_FAIL") == "1":
        return FailingChatModel()
    m = RuleChatModel()
    m.calls = []
    return m


def _failing_cls():
    from langchain_core.language_models import BaseChatModel

    class _Failing(BaseChatModel):
        @property
        def _llm_type(self) -> str:
            return "offline-failing"

        def _generate(self, messages, stop=None, run_manager=None, **kwargs):
            raise RuntimeError("오프라인 모의 오류 (오류 화면 확인용)")
    return _Failing


def FailingChatModel():  # noqa: N802 - 클래스처럼 쓰는 생성 함수
    return _failing_cls()()


def prepare_env_dir() -> Path:
    """KDCA_DATA_DIR에 합성 데이터와 위키 제목 파일을 만든다."""
    import json
    if not paths.OFFLINE:
        raise SystemExit("KDCA_OFFLINE_DEMO=1 과 KDCA_DATA_DIR=<임시 폴더> 를 설정한 뒤 실행하세요.")
    paths.ensure_dirs()
    prepare_data(paths.DATA)
    if not paths.WIKI_TITLES.exists():
        from data_prep.diseases import load_diseases
        did = next(d.disease_id for d in load_diseases(paths.DISEASES_CSV) if d.display_name == "홍역")
        paths.WIKI_TITLES.write_text(json.dumps({did: {"title": "홍역", "pageid": 100, "method": "exists"}},
                                                ensure_ascii=False), encoding="utf-8")
    return paths.DATA


if __name__ == "__main__":
    import sys
    if "--prepare" in sys.argv:
        print(f"[offline] 합성 데이터 준비: {prepare_env_dir()}")
