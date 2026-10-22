"""챗봇 답변 시간 측정·비교 → reports/bench_chat.md, reports/bench_chat.json

실제 측정 (GMS·위키백과 호출, 색인이 있어야 함):
  python -m scripts.bench_chat                                   # 기본 설정 6가지 비교
  python -m scripts.bench_chat --models gpt-4.1-mini,gpt-4.1-nano,gpt-4o-mini   # 답변 모델 비교
  python -m scripts.bench_chat --configs baseline,background --rounds 2
  python -m scripts.bench_chat --quick                           # baseline·background·prefetch만 (사용량 절약)
사용 한도 초과(429) 등으로 멈추면 그때까지의 결과로 보고서를 만든다 (질문마다 bench_chat.json에 저장).
네트워크 없이 흐름만 (가짜 LLM·위키에 지연을 흉내, 숫자는 모의값):
  python -m scripts.bench_chat --fake
화면(Streamlit) 자체가 더하는 시간:
  python -m scripts.bench_chat --fake --ui

측정 방법
- 설정마다 임시 작업 공간을 만든다: 지금 색인(data/chroma)을 복사하고, 시험 감염병 3종의 외부 문서·수집 캐시는 비운다.
- 감염병마다 질문 2개: 첫 질문 = cold(그 감염병 문서를 처음 모음), 둘째 질문 = warm(이미 모은 뒤).
- 기록: 총 시간, 첫 글자까지 시간(스트리밍 체감 대기), 단계별 시간, LLM·위키·임베딩 호출 수.
- 답변 캐시는 'cache' 설정에서만 켠다 (같은 질문을 다시 물어 측정).
"""
from __future__ import annotations

import argparse
import json
import shutil
import statistics
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path

from data_prep import settings as paths

QUESTIONS = [  # (감염병, cold 질문, warm 질문)
    ("홍역", "홍역은 어떻게 전파되나요?", "홍역의 초기 증상과 예방법은?"),
    ("쯔쯔가무시증", "쯔쯔가무시증은 어떤 병인가요?", "쯔쯔가무시증 잠복기는 얼마나 되나요?"),
    ("뎅기열", "뎅기열은 주로 어디서 발생하나요?", "뎅기열 치료와 예방은 어떻게 하나요?"),
]
CONFIGS = {   # 이름: (설명, 설정 덮어쓰기)
    "baseline": ("기존 방식: 매번 검색어 추출·수집, 순서대로, 스트리밍 없음",
                 {"collect_policy": "always", "parallel_collect": False, "stream_answer": False}),
    "parallel": ("수집 병렬화 (항목 문서·검색어별 동시)",
                 {"collect_policy": "always", "parallel_collect": True, "stream_answer": False}),
    "stream": ("병렬 + 답변 스트리밍 (첫 글자 대기 단축)",
               {"collect_policy": "always", "parallel_collect": True, "stream_answer": True}),
    "when_needed": ("병렬 + 스트리밍 + 이미 모은 문서가 있으면 수집 생략",
                    {"collect_policy": "when_needed", "parallel_collect": True, "stream_answer": True}),
    "background": ("병렬 + 스트리밍 + 바로 답하고 수집은 뒤에서 (권장)",
                   {"collect_policy": "background", "parallel_collect": True, "stream_answer": True}),
    "cache": ("background + 같은 질문 저장 답변",
              {"collect_policy": "background", "parallel_collect": True, "stream_answer": True, "answer_cache": True}),
    "prefetch": ("background + 감염병 문서 미리 색인 (build_index --prefetch-docs) → 첫 질문도 warm",
                 {"collect_policy": "background", "parallel_collect": True, "stream_answer": True, "_prefetch": True}),
}


# ---- 작업 공간 ---------------------------------------------------------------------------
def make_workspace(root: Path, fake: bool):
    """임시 색인·캐시 경로로 런타임을 만든다. 시험 감염병의 외부 문서는 비운다(cold)."""
    from service.runtime import build_runtime
    ws = Path(tempfile.mkdtemp(dir=root))
    paths.WIKI_PAGES = ws / "pages"
    paths.QUERY_CACHE = ws / "query_cache.json"
    paths.CACHE = ws / "cache"
    for p in (paths.WIKI_PAGES, paths.CACHE):
        p.mkdir(parents=True, exist_ok=True)
    if fake:
        return ws, None
    shutil.copytree(paths.ROOT / "data" / "chroma", ws / "chroma")
    return ws, build_runtime


def fake_runtime(ws: Path, settings: dict, lat: dict):
    """가짜 LLM·위키·임베딩에 지연을 넣은 런타임 (네트워크 없이 흐름 비교용)."""
    from langchain_core.embeddings import DeterministicFakeEmbedding
    from data_prep.collector import WikiCollector
    from data_prep.diseases import build_index_text, build_metadata, load_diseases
    from rag.answer_cache import AnswerCache
    from rag.graph import RagServices, build_graph
    from service.runtime import Runtime
    from tests.fakes import RuleChatModel, fake_wiki_client
    from vectorstore.store import DiseaseStore

    class SlowChat(RuleChatModel):
        def _generate(self, messages, stop=None, run_manager=None, **kw):
            system = messages[0].content
            time.sleep(lat["answer"] if "안내원" in system else lat["helper"])
            return super()._generate(messages, stop, run_manager, **kw)

        def _stream(self, messages, stop=None, run_manager=None, **kw):
            from langchain_core.messages import AIMessageChunk
            from langchain_core.outputs import ChatGenerationChunk
            time.sleep(lat["first_token"])
            text = super()._generate(messages, stop, run_manager, **kw).generations[0].message.content
            words = text.split(" ")
            for i, w in enumerate(words):
                time.sleep((lat["answer"] - lat["first_token"]) / max(len(words), 1))
                yield ChatGenerationChunk(message=AIMessageChunk(content=w + (" " if i < len(words) - 1 else "")))

    class SlowEmb(DeterministicFakeEmbedding):
        def embed_documents(self, texts):
            time.sleep(lat["embed"])
            return super().embed_documents(texts)

        def embed_query(self, text):
            time.sleep(lat["embed"])
            return super().embed_query(text)

    client, _ = fake_wiki_client()
    for name in ("search_candidates", "fetch_page"):
        orig = getattr(client, name)

        def slow(*a, _o=orig, _n=name, **k):
            time.sleep(lat["wiki_search" if _n == "search_candidates" else "wiki_fetch"])
            return _o(*a, **k)
        setattr(client, name, slow)
    settings = {**settings, "chroma_dir": str(ws / "chroma")}
    diseases = load_diseases(paths.DISEASES_CSV)
    store = DiseaseStore(settings, DeterministicFakeEmbedding(size=32))
    store.index_meta([build_index_text(d) for d in diseases], [build_metadata(d) for d in diseases])
    store.embeddings = SlowEmb(size=32)
    store.docs._embedding_function = store.embeddings
    by_id = {d.disease_id: d for d in diseases}
    titles = {d.disease_id: {"title": "홍역", "pageid": 100} for d in diseases}   # 가짜 위키는 '홍역' 문서만
    svc = RagServices(SlowChat(), store, WikiCollector(client, ws / "pages", ws / "qc.json"), by_id, titles, settings,
                      answer_cache=AnswerCache(ws / "answers.json") if settings.get("answer_cache") else None)
    return Runtime(settings, diseases, by_id, store, svc, build_graph(svc))


def clear_docs(rt, dids: list[str]) -> None:
    for did in dids:
        rt.store.docs._collection.delete(where={"disease_id": did})


# ---- 측정 --------------------------------------------------------------------------------
def ask(rt, q: str, did: str) -> dict:
    from rag.graph import run
    t0 = time.perf_counter()
    st = run(rt.graph, q, did)
    t = dict(st.get("timings", {}))
    t["wall_s"] = round(time.perf_counter() - t0, 3)
    t["answer_chars"] = len(st.get("answer", ""))
    return t


class QuotaError(RuntimeError):
    """GMS·OpenAI 사용 한도 초과 (429 insufficient_quota 등): 더 측정해도 실패하므로 멈추고 지금까지 저장."""


def is_quota(exc: BaseException) -> bool:
    text = f"{type(exc).__name__} {exc}"
    return any(k in text for k in ("insufficient_quota", "spend_limit", "RateLimitError", "429"))


def bench_config(name: str, overrides: dict, args, lat: dict, rows: list[dict], on_row=None) -> list[dict]:
    from data_prep.diseases import load_diseases
    base = paths.load_rag_settings()
    prefetch = overrides.get("_prefetch", False)
    settings = {**base, "answer_cache": False, **{k: v for k, v in overrides.items() if not k.startswith("_")}}
    if args.model:
        settings["chat_model"] = args.model
    if args.aux_model is not None:
        settings["aux_model"] = args.aux_model
    for r in range(args.rounds):
        root = Path(tempfile.mkdtemp(prefix="bench_"))
        rt = None
        try:
            ws, build = make_workspace(root, args.fake)
            if args.fake:
                rt = fake_runtime(ws, settings, lat)
            else:
                settings["chroma_dir"] = str(ws / "chroma")
                rt = build(settings=settings)
            by_name = {d.display_name: d.disease_id for d in load_diseases(paths.DISEASES_CSV)}
            dids = [by_name[n] for n, _, _ in QUESTIONS]
            clear_docs(rt, dids)
            if prefetch:   # 항목 자체 문서만 미리 색인 (시간 측정에서 제외)
                from rag.graph import _collect
                for did in dids:
                    _collect(rt.services, rt.by_id[did], "", [])
            for (n, q_cold, q_warm), did in zip(QUESTIONS, dids):
                try:
                    cold = ask(rt, q_cold, did)
                    _wait_background(rt)      # 배경 수집이 끝난 뒤 warm 측정 (실사용에서도 보통 질문 사이에 끝남)
                    warm = ask(rt, q_warm, did)
                    row = {"config": name, "round": r + 1, "disease": n, "cold": cold, "warm": warm}
                    if overrides.get("answer_cache"):
                        row["repeat"] = ask(rt, q_warm, did)
                except Exception as exc:  # noqa: BLE001
                    if is_quota(exc):
                        raise QuotaError(str(exc)[:300]) from exc
                    print(f"  {name:12s} {n:8s} 오류: {type(exc).__name__}: {str(exc)[:160]}")
                    rows.append({"config": name, "round": r + 1, "disease": n, "error": f"{type(exc).__name__}: {exc}"[:500]})
                    continue
                rows.append(row)
                if on_row:
                    on_row()
                print(f"  {name:12s} {n:8s} cold {cold['wall_s']:6.2f}s (첫 글자 {cold['first_token_s']:.2f}s, "
                      f"LLM {cold['llm_calls']}) · warm {warm['wall_s']:6.2f}s (첫 글자 {warm['first_token_s']:.2f}s, "
                      f"LLM {warm['llm_calls']})" + (f" · 반복 {row['repeat']['wall_s']:.2f}s" if 'repeat' in row else ""))
        finally:
            _wait_background(rt)   # 마지막 warm 질문이 시작한 배경 수집이 끝난 뒤 임시 폴더를 지운다
            shutil.rmtree(root, ignore_errors=True)
    return rows


def _wait_background(rt, timeout: float = 120) -> None:
    import threading
    if rt is None:
        return
    t0 = time.time()
    while rt.services.background and time.time() - t0 < timeout:
        time.sleep(0.1)
    for t in threading.enumerate():
        if t.name.startswith("collect-"):
            t.join(max(0.0, timeout - (time.time() - t0)))


def summarize(rows: list[dict]) -> list[dict]:
    out = []
    for name in dict.fromkeys(r["config"] for r in rows if "error" not in r):
        rs = [r for r in rows if r["config"] == name and "error" not in r]

        def avg(kind, key):
            return round(statistics.mean(r[kind][key] for r in rs), 2)
        s = {"config": name, "cold_s": avg("cold", "wall_s"), "cold_first_s": avg("cold", "first_token_s"),
             "warm_s": avg("warm", "wall_s"), "warm_first_s": avg("warm", "first_token_s"),
             "cold_llm": avg("cold", "llm_calls"), "warm_llm": avg("warm", "llm_calls"),
             "cold_wiki": round(statistics.mean(sum(v for k, v in r["cold"]["calls"].items() if k.startswith("wiki"))
                                                for r in rs), 1)}
        if all("repeat" in r for r in rs):
            s["repeat_s"] = avg("repeat", "wall_s")
        for kind in ("cold", "warm"):   # 단계별 평균 (어디서 시간이 드는지)
            stages = {}
            for r in rs:
                for k, v in r[kind].get("stages", {}).items():
                    stages.setdefault(k, []).append(v)
            s[f"{kind}_stages"] = {k: round(statistics.mean(v), 2) for k, v in stages.items()}
        s["n"] = len(rs)
        s["avg_s"] = round((s["cold_s"] + s["warm_s"]) / 2, 2)
        out.append(s)
    return out


def ui_overhead(lat: dict) -> dict:
    """Streamlit이 질문 하나에 더하는 시간: 화면 전체 실행 시간 - 그래프 시간 (가짜 런타임)."""
    import os
    from unittest import mock
    from streamlit.testing.v1 import AppTest
    import service.runtime as runtime_mod
    os.environ.setdefault("OPENAI_API_KEY", "x")
    os.environ.setdefault("WIKI_USER_AGENT", "bench (contact: bench@example.com)")
    root = Path(tempfile.mkdtemp(prefix="bench_ui_"))
    try:
        settings = {**paths.load_rag_settings(), "collect_policy": "background", "answer_cache": False}
        rt = fake_runtime(root, settings, lat)
        with mock.patch.object(runtime_mod, "build_runtime", lambda *a, **k: rt), \
                mock.patch("data_prep.settings.PROCESSED", root):
            at = AppTest.from_file(str(paths.ROOT / "ui" / "app.py"), default_timeout=120).run()
            at.switch_page("views/chat.py").run()
            samples = []
            for q in ["홍역은 어떻게 전파되나요?", "홍역 예방법은?", "홍역 초기 증상은?"]:
                t0 = time.perf_counter()
                at.chat_input[0].set_value(q).run()
                wall = time.perf_counter() - t0
                graph = at.session_state["chat_log"][-1]["timings"]["total_s"]
                samples.append((wall, graph))
        return {"page_s": round(statistics.mean(w for w, _ in samples), 3),
                "graph_s": round(statistics.mean(g for _, g in samples), 3),
                "overhead_s": round(statistics.mean(w - g for w, g in samples), 3)}
    finally:
        shutil.rmtree(root, ignore_errors=True)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--configs", default="baseline,parallel,stream,when_needed,background,cache,prefetch")
    ap.add_argument("--models", default="", help="쉼표로 여러 답변 모델 (background 설정으로 비교)")
    ap.add_argument("--model", default="", help=argparse.SUPPRESS)
    ap.add_argument("--aux-model", default=None, help="검색어 추출·선별 모델 (예: gpt-4.1-nano)")
    ap.add_argument("--rounds", type=int, default=1)
    ap.add_argument("--quick", action="store_true", help="baseline·background·prefetch만 (GMS 사용량 절약)")
    ap.add_argument("--fake", action="store_true", help="네트워크 없이 모의 지연으로")
    ap.add_argument("--ui", action="store_true", help="Streamlit 화면 자체 시간도 측정 (--fake와 함께)")
    args = ap.parse_args(argv)
    # 모의 지연(초): 실제 GMS·위키에서 흔한 범위의 대략값. --fake일 때만 쓰며 결과 표에 '모의'로 표시한다.
    lat = {"helper": 1.0, "answer": 3.0, "first_token": 0.7, "wiki_search": 0.5, "wiki_fetch": 0.6, "embed": 0.3}

    paths.ensure_dirs()
    orig = (paths.WIKI_PAGES, paths.QUERY_CACHE, paths.CACHE)
    rows: list[dict] = []
    stopped = ""

    def save_partial() -> None:   # 질문마다 저장 → 중간에 멈춰도 결과가 남음
        (paths.REPORTS / "bench_chat.json").write_text(
            json.dumps({"partial": True, "rows": rows}, ensure_ascii=False, indent=1), encoding="utf-8")
    configs = ["baseline", "background", "prefetch"] if args.quick else \
        [c.strip() for c in args.configs.split(",") if c.strip()]
    try:
        if args.models:
            for m in [x.strip() for x in args.models.split(",") if x.strip()]:
                args.model = m
                print(f"[model] {m}")
                bench_config(f"model:{m}", CONFIGS["background"][1], args, lat, rows, save_partial)
        else:
            for name in configs:
                print(f"[config] {name}: {CONFIGS[name][0]}")
                bench_config(name, CONFIGS[name][1], args, lat, rows, save_partial)
    except QuotaError as exc:
        stopped = f"사용 한도 초과로 중단 (GMS/OpenAI 429): {exc}"
        print(f"\n[!] {stopped}\n    지금까지 측정한 결과로 보고서를 만듭니다.")
    except KeyboardInterrupt:
        stopped = "사용자가 중단"
    finally:
        paths.WIKI_PAGES, paths.QUERY_CACHE, paths.CACHE = orig
    summary = summarize(rows)
    ui = ui_overhead(lat) if args.ui else None
    if not summary:
        print("[x] 측정된 결과가 없습니다.")
        return 1

    mode = "모의 지연 (--fake, 네트워크 없음)" if args.fake else "실제 호출 (GMS·위키백과)"
    s0 = next((s for s in summary if s["config"] == "baseline"), summary[0])
    best = min(summary, key=lambda s: s["avg_s"])
    lines = [f"# 챗봇 답변 시간 측정 ({datetime.now():%Y-%m-%d %H:%M}, {mode})", "",
             f"- 질문: 감염병 {len(QUESTIONS)}종 × (cold 1 + warm 1), 반복 {args.rounds}회 평균",
             f"- 답변 모델: {args.model or paths.load_rag_settings()['chat_model']}"
             + (f", 보조 모델: {args.aux_model}" if args.aux_model else "")]
    if stopped:
        lines.append(f"- **{stopped}** · 측정 못 한 설정은 표에 없음")
    lines += ["", "| 설정 | cold 총 | cold 첫 글자 | warm 총 | warm 첫 글자 | 평균 | LLM 호출 cold/warm | 위키 호출 cold | 기존 대비 |",
              "| --- | --- | --- | --- | --- | --- | --- | --- | --- |"]
    for s in summary:
        cut = (1 - s["avg_s"] / s0["avg_s"]) * 100 if s0["avg_s"] else 0
        vs = "-" if s is s0 else (f"{cut:.0f}% 단축" if cut >= 0 else f"{-cut:.0f}% 느림")
        label = s["config"] if s["n"] == len(QUESTIONS) * args.rounds else f"{s['config']} ({s['n']}건)"
        lines.append(f"| {label} | "
                     f"{s['cold_s']:.2f}s | {s['cold_first_s']:.2f}s | {s['warm_s']:.2f}s | "
                     f"{s['warm_first_s']:.2f}s | **{s['avg_s']:.2f}s** | {s['cold_llm']:.0f} / {s['warm_llm']:.0f} | "
                     f"{s['cold_wiki']:.0f} | {vs} |"
                     + (f" 같은 질문 반복 {s['repeat_s']:.2f}s" if "repeat_s" in s else ""))
    lines += ["", f"**가장 빠른 설정: {best['config']}** (평균 {best['avg_s']:.2f}s, 기존 {s0['avg_s']:.2f}s 대비 "
              f"{(1 - best['avg_s'] / s0['avg_s']) * 100 if s0['avg_s'] else 0:.0f}% 단축)", "",
              "단계별 평균 (초):", "", "| 설정 | cold: 검색어 · 수집 · 근거 검색 · 답변 | warm: 검색어 · 수집 · 근거 검색 · 답변 |",
              "| --- | --- | --- |"]
    order = ("queries", "collect", "search", "generate")
    for s in summary:
        lines.append(f"| {s['config']} | " + " · ".join(f"{s['cold_stages'].get(k, 0):.1f}" for k in order)
                     + " | " + " · ".join(f"{s['warm_stages'].get(k, 0):.1f}" for k in order) + " |")
    errors = [r for r in rows if "error" in r]
    if errors:
        lines += ["", "오류:", *[f"- {r['config']} {r['disease']}: {r['error'][:200]}" for r in errors]]
    if ui:
        lines += ["", f"- Streamlit 화면이 더하는 시간: 질문당 {ui['overhead_s']:.2f}s "
                      f"(화면 전체 {ui['page_s']:.2f}s 중 RAG {ui['graph_s']:.2f}s)"]
    lines += ["", "설정 설명:", *[f"- {k}: {v[0]}" for k, v in CONFIGS.items()]]
    report = paths.REPORTS / "bench_chat.md"
    report.write_text("\n".join(lines) + "\n", encoding="utf-8")
    (paths.REPORTS / "bench_chat.json").write_text(
        json.dumps({"mode": mode, "stopped": stopped, "summary": summary, "ui": ui, "rows": rows},
                   ensure_ascii=False, indent=1), encoding="utf-8")
    print("\n" + "\n".join(lines))
    return 0


if __name__ == "__main__":
    sys.exit(main())
