"""RAG 사용 전후 답변 비교 (F2015, 요구사항 정의서 4.6).

python -m scripts.compare_rag              # 실제 모델(.env)로 비교 → docs/RAG_COMPARISON.md, reports/rag_comparison.json
python -m scripts.compare_rag --offline    # 키·네트워크 없이 흐름만 확인 (가짜 모델, reports/에만 저장)

대표 질문은 '근거 없이 답하면 틀리기 쉬운' 것으로 고른다.
- 개정으로 바뀐 급수·신고 기한 (예: 코로나19, 엠폭스, CRE 감염증) - 모델의 학습 시점 지식과 다를 수 있음
- KCD 코드, 담당 부서처럼 외워 두기 어려운 사실
- 문서에 없는 것 (약가, 특정 구 단위 환자 수) - 모른다고 답해야 정답
- 검역관리지역 지정 국가 (공공데이터 API 문서, F2016) - 날짜에 따라 바뀌는 사실

정답은 사람이 쓰지 않고 프로젝트 데이터(감염병포털 법정감염병 목록 CSV, 검역관리지역 API 결과)에서 만든다.
판정 기준 3가지 (자동 채점 + 사람이 확인할 칸)
  사실 정확성 : 답의 첫 급수·코드·기한·부서가 정답과 같은가 / 모름 질문은 모른다고 했는가
  근거 제시   : 답에 [근거 n]이 있는가 (RAG 없이는 출처를 댈 수 없음)
  모름 표명   : 문서에 없는 질문에 '확인되지 않습니다' 등으로 답했는가
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompts import ChatPromptTemplate

from data_prep import settings as paths

NO_RAG = ChatPromptTemplate.from_messages([
    ("system", "너는 한국의 법정감염병을 설명하는 공중보건 안내원이다. 질문에 한국어로 간결하게 답한다. "
               "확실하지 않은 내용은 모른다고 답한다. 말투는 '~입니다/~합니다' 체."),
    ("human", "{question}"),
])
ABSTAIN = ("확인되지 않", "알 수 없", "확인할 수 없", "정보가 없", "모릅니다", "제공된 문서", "알려져 있지 않",
           "답변드리기 어렵", "확인이 어렵", "공개되어 있지 않", "포함되어 있지 않")
CITE = re.compile(r"\[근거\s*\d")
GRADE = re.compile(r"제?\s*([1-4])\s*급")


@dataclass
class Case:
    id: str
    disease: str                  # 표시 이름
    question: str
    kind: str                     # grade | kcd | deadline | dept | abstain | countries
    expected: str
    why: str                      # 왜 틀리기 쉬운가
    accept: list[str] = field(default_factory=list)


def josa(word: str, pair: str) -> str:
    """받침에 맞는 조사 ('은는', '을를', '이가', '와과'). 괄호 속 영문은 건너뛰고 마지막 한글로 판단."""
    base = re.sub(r"\([^)]*\)\s*$", "", word).strip() or word
    if base[-1].isdigit():          # 숫자로 끝나면 읽는 소리로 (19 → 구)
        has_final = base[-1] in "013678"
    else:
        ch = next((c for c in reversed(base) if "가" <= c <= "힣"), "")
        has_final = bool(ch) and (ord(ch) - 0xAC00) % 28 != 0
    return word + (pair[0] if has_final else pair[1])


def build_cases(diseases, watch=None) -> list[Case]:
    by = {d.display_name: d for d in diseases}
    out: list[Case] = []

    def add(name, kind, q, why):
        d = by.get(name)
        if d is None:
            return
        exp = {"grade": f"제{d.grade}급", "kcd": d.kcd, "deadline": d.rule["deadline"], "dept": d.department,
               "abstain": "확인되지 않음"}[kind]
        out.append(Case(f"Q{len(out) + 1}", name, q.format(n=name, n_eun=josa(name, "은는"), n_eul=josa(name, "을를")),
                        kind, exp, why))

    add("코로나바이러스감염증-19", "grade", "{n_eun} 현재 몇 급 법정감염병인가요?", "제2급에서 제4급으로 하향 (학습 시점에 따라 제2급으로 답하기 쉬움)")
    add("엠폭스(Mpox)", "grade", "{n_eun} 몇 급 법정감염병인가요?", "제1급 → 제2급 → 제3급으로 여러 번 바뀜")
    add("카바페넴내성장내세균목(CRE) 감염증", "grade", "{n_eun} 몇 급 법정감염병인가요?", "2020년 급수 체계 개편 전후 분류가 달라 헷갈리기 쉬움")
    add("쯔쯔가무시증", "kcd", "{n}의 KCD(한국표준질병사인분류) 코드는 무엇인가요?", "외워 두기 어려운 코드")
    add("성홍열", "deadline", "{n_eun} 발생하면 언제까지 신고해야 하나요?", "급수에 따라 즉시/24시간/7일로 갈림")
    add("엠폭스(Mpox)", "dept", "질병관리청에서 {n_eul} 담당하는 부서는 어디인가요?", "모델이 알 수 없는 조직 정보")
    add("뎅기열", "abstain", "{n} 치료제의 국내 건강보험 약가는 얼마인가요?", "문서에 없음 → 모른다고 해야 정답")
    add("홍역", "abstain", "2026년 10월 서울 강남구의 {n} 확진자 수는 몇 명인가요?", "문서에 없음 → 모른다고 해야 정답")
    if watch is not None and watch.loaded:
        for name in ("황열", "뎅기열", "콜레라"):
            d = by.get(name)
            if not d:
                continue
            countries = sorted({c.name_ko for c in watch.countries.values()
                                for x in c.diseases if x.get("disease_id") == d.disease_id})
            if countries:
                out.append(Case(f"Q{len(out) + 1}", name, f"{name} 때문에 검역관리지역으로 지정된 나라를 알려 주세요.",
                                "countries", f"{watch.as_of} 기준 {len(countries)}개국",
                                "날짜에 따라 바뀌는 지정 현황 (공공데이터 API)", countries))
                break
    return out


def _norm(s: str) -> str:
    return re.sub(r"[\s.,()·\-~]", "", s).upper()


def judge(case: Case, answer: str) -> dict:
    a = answer or ""
    abstained = any(k in a for k in ABSTAIN)
    if case.kind == "grade":
        m = GRADE.search(a)
        ok = bool(m) and f"제{m.group(1)}급" == case.expected
    elif case.kind == "kcd":
        first = _norm(case.expected.split(",")[0].split("~")[0])
        ok = first in _norm(a)
    elif case.kind == "deadline":
        ok = case.expected.replace(" 이내", "") in a
    elif case.kind == "dept":
        ok = _norm(case.expected) in _norm(a)
    elif case.kind == "countries":
        hit = [c for c in case.accept if c in a]
        ok = len(hit) >= min(3, len(case.accept))
    else:   # abstain
        ok = abstained
    return {"correct": ok, "cited": bool(CITE.search(a)), "abstained": abstained}


def run_cases(cases, rt, use_rag: bool) -> list[dict]:
    from rag.graph import run
    rows = []
    for c in cases:
        did = next(d.disease_id for d in rt.diseases if d.display_name == c.disease)
        t0 = time.perf_counter()
        try:
            if use_rag:
                st = run(rt.graph, c.question, did)
                ans, cited = st.get("answer", ""), [f"[근거 {e['n']}] {e['source_label']} · {e['title']}"
                                                    for e in st.get("cited", [])]
            else:
                ans, cited = (NO_RAG | rt.services.llm | StrOutputParser()).invoke({"question": c.question}).strip(), []
            err = ""
        except Exception as exc:  # noqa: BLE001 - 한도 초과 등은 기록하고 계속
            ans, cited, err = "", [], type(exc).__name__
        rows.append({"answer": ans, "cited_list": cited, "error": err, "sec": round(time.perf_counter() - t0, 2),
                     **judge(c, ans)})
    return rows


def _cell(s: str, n: int = 400) -> str:
    s = re.sub(r"\s+", " ", s or "").strip()
    s = s if len(s) <= n else s[:n] + "…"
    return s.replace("|", "\\|")


def write_report(cases, base, rag, path: Path, offline: bool, model: str) -> str:
    def score(rows, k):
        return sum(r[k] for r in rows)

    n = len(cases)
    n_abs = sum(c.kind == "abstain" for c in cases)
    lines = [
        "# RAG 사용 전후 답변 비교 (F2015)", "",
        f"- 실행: {datetime.now():%Y-%m-%d %H:%M} · 모델 `{model}`" + (" · **오프라인 모의 실행 (가짜 모델, 실제 결과 아님)**" if offline else ""),
        f"- 대표 질문 {n}개 (근거 없이 답하면 틀리기 쉬운 질문). 정답은 감염병포털 법정감염병 목록 CSV·검역관리지역 API 결과에서 자동 생성",
        "- RAG 없이: 같은 모델에 질문만 전달 (\"확실하지 않으면 모른다고 답한다\" 규칙은 같게 줌)",
        "- RAG 사용: 이 서비스의 파이프라인 (항목 메타데이터 + 외부 문서 근거, 프롬프트 3)",
        "- 다시 만들기: `python -m scripts.compare_rag`", "",
        "## 요약", "",
        "| 기준 | RAG 없이 | RAG 사용 |", "| --- | --- | --- |",
        f"| 사실 정확성 (정답 수) | {score(base, 'correct')}/{n} | {score(rag, 'correct')}/{n} |",
        f"| 근거 제시 (답에 [근거 n]) | {score(base, 'cited')}/{n} | {score(rag, 'cited')}/{n} |",
        f"| 모름 표명 (문서에 없는 질문 {n_abs}개 중) | "
        f"{sum(r['abstained'] for c, r in zip(cases, base) if c.kind == 'abstain')}/{n_abs} | "
        f"{sum(r['abstained'] for c, r in zip(cases, rag) if c.kind == 'abstain')}/{n_abs} |",
        f"| 평균 응답 시간 | {sum(r['sec'] for r in base) / max(n, 1):.1f}s | {sum(r['sec'] for r in rag) / max(n, 1):.1f}s |",
        "", "## 질문별 판정", "",
        "| # | 감염병 | 질문 | 정답(데이터) | 틀리기 쉬운 이유 | RAG 없이 정확/근거/모름 | RAG 사용 정확/근거/모름 |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    mark = lambda r: f"{'O' if r['correct'] else 'X'} / {'O' if r['cited'] else 'X'} / {'O' if r['abstained'] else '-'}" + (f" ({r['error']})" if r["error"] else "")  # noqa: E731
    for c, b, r in zip(cases, base, rag):
        lines.append(f"| {c.id} | {c.disease} | {_cell(c.question, 80)} | {_cell(c.expected, 60)} | {c.why} | {mark(b)} | {mark(r)} |")
    lines += ["", "## 답변 원문 (나란히)", ""]
    for c, b, r in zip(cases, base, rag):
        lines += [f"### {c.id}. {c.question}", "", f"정답(데이터): {c.expected}" + (f" — {', '.join(c.accept[:10])}" if c.accept else ""), "",
                  "| RAG 없이 | RAG 사용 |", "| --- | --- |",
                  f"| {_cell(b['answer'], 700) or '(오류: ' + b['error'] + ')'} | {_cell(r['answer'], 700) or '(오류: ' + r['error'] + ')'} |", ""]
        if r["cited_list"]:
            lines += ["RAG 근거: " + " / ".join(r["cited_list"]), ""]
    lines += ["## 해석 (사람이 확인해 적는 칸)", "",
              "- 자동 판정은 정답 문자열 포함 여부로 하므로, 표의 X 중 표현만 다른 경우가 있는지 원문을 보고 확인한다.",
              "- RAG 없이 틀린 질문은 대부분 개정된 급수·코드처럼 학습 시점 이후 바뀌었거나 외워 두기 어려운 사실이다.",
              "- RAG 사용 답은 항목 메타데이터 근거([근거 1])로 급수·신고 기한·코드를 맞히고, 문서에 없는 질문은 "
              "\"제공된 문서에서 확인되지 않습니다\"로 답한다.", ""]
    text = "\n".join(lines)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return text


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--offline", action="store_true", help="가짜 모델·임시 색인으로 흐름만 확인")
    ap.add_argument("--out", help="결과 문서 경로 (기본 docs/RAG_COMPARISON.md, --offline이면 reports/)")
    args = ap.parse_args(argv)

    from service.countries import load_watch
    if args.offline:
        from scripts.offline_env import offline_runtime
        rt, cleanup = offline_runtime()
        model = "offline-fake"
    else:
        from service.runtime import build_runtime, setup_logging
        setup_logging()
        settings = paths.load_rag_settings()
        settings.update(answer_cache=False, warmup=False, collect_policy="when_needed")
        rt, cleanup, model = build_runtime(settings), (lambda: None), settings["chat_model"]
    try:
        watch = load_watch(paths.PROCESSED / "country_watch.json")
        cases = build_cases(rt.diseases, watch)
        print(f"대표 질문 {len(cases)}개")
        base = run_cases(cases, rt, use_rag=False)
        rag = run_cases(cases, rt, use_rag=True)
        out = Path(args.out) if args.out else (paths.REPORTS / "rag_comparison_offline.md" if args.offline
                                               else paths.ROOT / "docs" / "RAG_COMPARISON.md")
        write_report(cases, base, rag, out, args.offline, model)
        (paths.REPORTS / ("rag_comparison_offline.json" if args.offline else "rag_comparison.json")).write_text(
            json.dumps([{"case": c.__dict__, "no_rag": b, "rag": r} for c, b, r in zip(cases, base, rag)],
                       ensure_ascii=False, indent=2), encoding="utf-8")
        for c, b, r in zip(cases, base, rag):
            print(f"  {c.id} {c.disease:<12} 정확 {int(b['correct'])}→{int(r['correct'])}  근거 {int(b['cited'])}→{int(r['cited'])}")
        print(f"[저장] {out.relative_to(paths.ROOT) if out.is_relative_to(paths.ROOT) else out}")
    finally:
        cleanup()
    return 0


if __name__ == "__main__":
    sys.exit(main())
