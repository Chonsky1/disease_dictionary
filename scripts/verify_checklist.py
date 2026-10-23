"""셀프 체크리스트 검증·증빙 자동 생성 (요구사항 정의서 4장, 체크리스트 27항목).

python -m scripts.verify_checklist --xlsx "AI코어_셀프체크리스트.xlsx"                 # 전체 (실제 모델, 화면 캡처 포함)
python -m scripts.verify_checklist --xlsx ... --no-screens                              # 화면 캡처 없이
python -m scripts.verify_checklist --xlsx ... --only 1,2                               # 일부 항목만 다시 (나머지는 지난 결과 유지)
python -m scripts.verify_checklist --xlsx ... --fill-only                              # 다시 실행하지 않고 지난 결과로 xlsx만
python -m scripts.verify_checklist --xlsx ... --fill-only --import-log run1.txt --import-log run2.txt
                                                       # 콘솔 출력을 저장해 둔 파일에서 결과 복원 (뒤 파일이 우선)
python -m scripts.verify_checklist --xlsx ... --offline                                # 키 없이 도구 동작만 확인 (제출용 아님)

하는 일
 1) 항목마다 확인 방법대로 '실제 명령'을 실행하고(스크립트를 subprocess로) 출력 전체를 증빙 파일로 저장
    submission/evidence/NN_<요구사항>_<내용>.txt (+ 같은 이름 .png: 터미널 화면처럼 그린 캡처)
 2) 화면 항목은 scripts/capture_screens.py로 앱을 띄워 조작하고 PNG 저장 (S08, S10a … )
 3) 출력에서 기대 결과를 자동 판정 → 통과 / 실패 / 미확인 / 해당없음 + 비고(사유)
 4) 체크리스트 xlsx 사본의 결과·증빙·비고 칸을 채움 → submission/셀프체크리스트_결과.xlsx
 5) 검증 결과 문서: submission/검증결과/요구사항_대조표.md, 테스트_결과표.md, 검증_요약.md

주의
 - 실제 모드는 GMS(채팅·임베딩)와 위키백과를 호출한다. 색인 스크립트를 세 번(F2001·F2002·F2005) 실행하고,
   F2005는 청크 설정을 바꿔 재색인한 뒤 원래 설정으로 되돌린다 (임베딩 호출 발생).
 - 확인하지 않은 것을 통과로 적지 않는다: 실행 실패·시간 초과·판정 불가는 '미확인' + 사유.
 - 증빙에 키 값이 들어가지 않는다 (스크립트들이 키를 출력하지 않고, 마지막에 scan_secrets로 제출 폴더도 검사).
"""
from __future__ import annotations

import argparse
import html
import json
import os
import re
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from data_prep import settings as paths

PY = sys.executable
PASS, FAIL, UNK, NA = "통과", "실패", "미확인", "해당없음"


@dataclass
class Result:
    no: int
    req: str
    status: str = UNK
    files: list[str] = field(default_factory=list)
    note: str = ""


@dataclass
class Run:
    cmd: str
    out: str
    code: int
    sec: float


class Verifier:
    def __init__(self, out: Path, offline: bool, screens: bool, env: dict):
        self.out, self.offline, self.screens = out, offline, screens
        self.ev = out / "evidence"
        self.ev.mkdir(parents=True, exist_ok=True)
        self.env = env
        self.results: dict[int, Result] = {}
        self.shots: dict[str, dict] = {}
        self.cache: dict[str, Run] = {}
        self.disease, self.question = ("홍역", "홍역은 어떻게 전파되나요?") if offline else \
            ("브루셀라증", "브루셀라증은 사람에게 어떻게 옮나요?")

    # ---- 실행·증빙 --------------------------------------------------------------------------
    def run(self, args: list[str], key: str | None = None, timeout: int = 1800, extra_env: dict | None = None) -> Run:
        if key and key in self.cache:
            return self.cache[key]
        cmd = "python -m " + " ".join(f'"{a}"' if " " in a else a for a in args)
        t0 = time.perf_counter()
        try:
            p = subprocess.run([PY, "-m", *args], cwd=paths.ROOT, env={**self.env, **(extra_env or {})},
                               capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout)
            merge = args[0] == "unittest"      # unittest는 결과를 stderr로 쓴다
            out = p.stdout + ((("\n" if merge else "\n[stderr]\n") + p.stderr[-6000:]) if (merge or p.returncode) and p.stderr else "")
            code = p.returncode
        except subprocess.TimeoutExpired:
            out, code = f"[시간 초과 {timeout}s]", -1
        r = Run(cmd, out, code, round(time.perf_counter() - t0, 1))
        print(f"   $ {cmd}  ({r.sec}s, 종료 코드 {code})", flush=True)
        if key:
            self.cache[key] = r
        return r

    def save(self, name: str, runs: list[Run], head: str = "", tail: str = "") -> list[str]:
        mode = "오프라인 모의 실행 (가짜 모델 · 제출용 아님)" if self.offline else "실제 실행"
        parts = [f"# {name}", f"# {datetime.now():%Y-%m-%d %H:%M:%S} · {mode}", ""]
        if head:
            parts += [head, ""]
        for r in runs:
            parts += [f"$ {r.cmd}", f"(소요 {r.sec}s, 종료 코드 {r.code})", r.out.rstrip(), ""]
        if tail:
            parts += [tail, ""]
        txt = self.ev / f"{name}.txt"
        txt.write_text("\n".join(parts), encoding="utf-8")
        files = [txt.name]
        png = self.ev / f"{name}.png"
        if text_to_png(txt, png):
            files.insert(0, png.name)
        return files

    def set(self, no: int, req: str, status: str, files: list[str], note: str) -> None:
        if self.offline and status == PASS:
            note = "[오프라인 모의: 도구 동작 확인용] " + note
        self.results[no] = Result(no, req, status, files, note)
        print(f"  [{status}] {no:>2} {req} · {note[:110]}", flush=True)

    def shot(self, sid: str) -> dict:
        return self.shots.get(sid, {})

    def shot_files(self, *ids: str) -> list[str]:
        return [self.shots[i]["file"] for i in ids if self.shots.get(i, {}).get("file")]


# ---- 출력 해석 ---------------------------------------------------------------------------------
def parse_ask(out: str) -> dict:
    q = re.search(r"\[프롬프트 1\] 검색어: (.*)", out)
    rows = re.findall(r"\[수집\] \((\w+)/(캐시|수집)\) (.+?) → 색인 (.+?) / 버림 (.+?) / 청크 (\d+)", out)
    ev = re.findall(r"^\s*\[근거 (\d+)\] (.+?) · (.+)$", out, flags=re.M)
    ans = out.split("[답변]", 1)[-1].split("[인용된 근거]", 1)[0].strip() if "[답변]" in out else ""
    cited = re.search(r"\[인용된 근거\] (.*)", out)
    sec = re.search(r"\[시간\] ([\d.]+)초", out)
    return {"queries": q.group(1).strip() if q else "", "rows": rows, "evidence": ev, "answer": ans,
            "cited": cited.group(1) if cited else "", "sec": float(sec.group(1)) if sec else None,
            "answer_nums": sorted({int(n) for n in re.findall(r"\[근거\s*([\d,\s]+)\]", ans) for n in
                                   re.findall(r"\d+", n)})}


def counts(out: str) -> tuple[int, int, int] | None:
    m = re.search(r"로드한 항목 (\d+)건 / \S+ 청크 (\d+)건 / \S+ 청크 (\d+)건", out)
    return tuple(int(x) for x in m.groups()) if m else None


def err_line(out: str) -> str:
    """실패 출력에서 원인 한 줄 ([x] 안내 또는 마지막 예외 줄). 키 값은 스크립트가 출력하지 않는다."""
    lines = [l.strip() for l in out.splitlines() if l.strip()]
    for l in reversed(lines):
        if l.startswith("[x]"):
            return l[:160]
    for l in reversed(lines):
        if re.match(r"^[\w.]+(Error|Exception)\b", l):
            return l[:160]
    return lines[-1][:160] if lines else ""


def text_to_png(txt: Path, png: Path, max_lines: int = 260) -> bool:
    """터미널 화면처럼 그린 캡처 (Playwright가 없으면 건너뜀)."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return False
    lines = txt.read_text(encoding="utf-8").splitlines()
    if len(lines) > max_lines:
        lines = lines[:max_lines] + [f"… (이하 {len(lines) - max_lines}줄은 {txt.name}에)"]
    body = html.escape("\n".join(lines))
    page_html = ("<html><body style='margin:0;background:#0d1117'><pre style=\"margin:0;padding:18px 22px;"
                 "font:13px/1.5 'D2Coding','Consolas','Noto Sans Mono CJK KR','Noto Sans CJK KR',monospace;"
                 "color:#e6edf3;white-space:pre-wrap;word-break:break-all;width:1180px\">" + body + "</pre></body></html>")
    try:
        with sync_playwright() as pw:
            b = pw.chromium.launch()
            p = b.new_page(viewport={"width": 1224, "height": 400})
            p.set_content(page_html)
            p.screenshot(path=str(png), full_page=True)
            b.close()
        return True
    except Exception:  # noqa: BLE001 - 브라우저가 없으면 텍스트 증빙만
        return False


# ---- 항목별 검증 ---------------------------------------------------------------------------------
def check_index(v: Verifier) -> None:
    before = v.run(["scripts.inspect_index", "--disease", "홍역"], "inspect_before")
    b1 = v.run(["scripts.build_index"], "build1", timeout=3600)
    if b1.code:      # 임베딩 API 일시 오류 등: 한 번 더 (이미 넣은 것은 건너뛰므로 빠름)
        print(f"     색인 실패 → 한 번 더 실행: {err_line(b1.out)}")
        b1 = v.run(["scripts.build_index"], timeout=3600)
        v.cache["build1"] = b1
    mid = v.run(["scripts.inspect_index", "--disease", "홍역"], "inspect_mid")
    b2 = v.run(["scripts.build_index"], "build2", timeout=3600)
    if b2.code:
        print(f"     색인 실패 → 한 번 더 실행: {err_line(b2.out)}")
        b2 = v.run(["scripts.build_index"], timeout=3600)
        v.cache["build2"] = b2
    after = v.run(["scripts.inspect_index", "--disease", "홍역"], "inspect_after")

    # 1. F2001
    m = re.search(r"\[load\] 감염병 (\d+)건", b1.out)
    idx = re.search(r"\[index\] \S+: \d+건 → (\d+)건", b1.out)
    c = counts(mid.out)
    text = mid.out.split("색인 텍스트:", 1)[-1].split("이 감염병으로 색인된 문서", 1)[0]
    attrs = ["급수", "신고 시기", "신고 범위", "KCD 코드", "담당부서"]
    missing = [a for a in attrs if a not in text]
    files = v.save("01_F2001_로드건수_색인건수_색인텍스트", [b1, mid])
    if b1.code or not (m and idx and c):
        v.set(1, "F2001", UNK, files, f"색인 스크립트 실행 실패 (종료 코드 {b1.code}): {err_line(b1.out)}")
    else:
        ok = int(m.group(1)) == int(idx.group(1)) == c[0] == c[1] and not missing
        v.set(1, "F2001", PASS if ok else FAIL, files,
              f"로드 {m.group(1)}건 = 메타 컬렉션 {c[1]}건, 색인 텍스트에 {', '.join(attrs)} 포함"
              if ok else f"로드 {m.group(1)} / 색인 {c[1]} / 빠진 속성 {missing}")

    # 2. F2002
    c0, c1, c2 = counts(before.out), counts(mid.out), counts(after.out)
    files = v.save("02_F2002_두번실행_청크수", [before, b1, mid, b2, after],
                   tail=f"[비교] 1회 실행 후 {c1} → 2회 실행 후 {c2} (로드 항목, 메타 청크, 문서 청크)")
    if not (c1 and c2) or b2.code:
        v.set(2, "F2002", UNK, files, f"색인 스크립트 실행 실패: {err_line(b2.out if b2.code else b1.out)}")
    else:
        ok = c1 == c2
        v.set(2, "F2002", PASS if ok else FAIL, files,
              f"1회 후 메타 {c1[1]}·문서 {c1[2]}청크 → 2회 후 메타 {c2[1]}·문서 {c2[2]}청크 (변화 없음)"
              if ok else f"청크 수가 달라짐 {c1} → {c2}")

    # 7. F2007
    mer = after.out.split("[LangGraph 구조]", 1)[-1]
    nodes = all(k in mer for k in ("retrieve", "generate"))
    edges = re.search(r"__start__\s*-->\s*retrieve", mer) and re.search(r"retrieve\s*-->\s*generate", mer) and \
        re.search(r"generate\s*-->\s*__end__", mer)
    code = (paths.ROOT / "rag" / "graph.py").read_text(encoding="utf-8")
    snippet = code[code.index("def build_graph"):code.index("def run(")]
    extra = [n for n in re.findall(r'add_node\("(\w+)"', code) if n not in ("retrieve", "generate")]
    files = v.save("07_F2007_LangGraph_구조", [after], tail="[코드] rag/graph.py\n" + snippet)
    v.set(7, "F2007", PASS if nodes and edges and not extra else FAIL, files,
          "START → retrieve → generate → END 두 노드 (Mermaid 출력 + 코드)" if nodes and edges and not extra
          else f"노드 구성이 다름 (추가 노드 {extra})")
    v.state_counts = c2


def check_collect(v: Verifier) -> None:
    args = ["scripts.ask", "--disease", v.disease, "--policy", "always", v.question]
    r1 = v.run(args, "ask1")
    r2 = v.run(args, "ask2")
    a1, a2 = parse_ask(r1.out), parse_ask(r2.out)
    # 3. F2003·F2004
    files = v.save("03_F2003_F2004_검색어_선별결과", [r1])
    q_rows = [r for r in a1["rows"] if r[0] == "query"]
    if r1.code:
        v.set(3, "F2003 · F2004", UNK, files, f"ask 실행 실패 (종료 코드 {r1.code})")
    else:
        sel = sum(r[3] != "-" for r in q_rows)
        rej = sum(r[4] != "-" for r in q_rows)
        ok = a1["queries"] not in ("", "[]") and bool(q_rows)
        v.set(3, "F2003 · F2004", PASS if ok else FAIL, files,
              f"검색어 {a1['queries']} · 검색어별 색인 문서/버린 문서를 나란히 출력 (색인 {sel}줄, 버림 {rej}줄)"
              if ok else "검색어 또는 선별 결과가 출력되지 않음")
    # 4. F2004·NF2002
    q2 = [r for r in a2["rows"] if r[0] == "query"]
    files = v.save("04_F2004_NF2002_재질문_수집로그", [r1, r2],
                   tail=f"[비교] 처리 시간 1회 {a1['sec']}초 → 2회 {a2['sec']}초 · 2회째 검색어 행: "
                        + ", ".join(f"{r[2]}({r[1]})" for r in q2))
    if r2.code or not q2:
        v.set(4, "F2004 · NF2002", UNK, files, "두 번째 실행 실패 또는 검색어 행 없음")
    else:
        cached = all(r[1] == "캐시" for r in q2) and "위키백과 검색:" not in r2.out
        same_q = a1["queries"] == a2["queries"]
        v.set(4, "F2004 · NF2002", PASS if cached else FAIL, files,
              f"2회째 모든 검색어가 '캐시'(위키 검색·선별 호출 없음), 시간 {a1['sec']}s → {a2['sec']}s"
              if cached else ("검색어가 달라져 새로 수집함 (LLM 검색어 변동)" if not same_q else "같은 검색어인데 다시 수집함"))
    # 8. F2008
    ev_nums = {int(n) for n, _, _ in a1["evidence"]}
    labels = {lab for _, lab, _ in a1["evidence"]}
    cited_ok = set(a1["answer_nums"]) <= ev_nums and a1["answer_nums"]
    kinds_ok = "항목 메타데이터" in labels and len(labels) >= 2
    files = v.save("08_F2008_근거번호_근거목록", [r1], tail=f"[대조] 답변의 근거 번호 {a1['answer_nums']} ⊂ 근거 목록 "
                   f"{sorted(ev_nums)} · 출처 표시 {sorted(labels)}") + v.shot_files("S08")
    if r1.code:
        v.set(8, "F2008", UNK, files, "ask 실행 실패")
    else:
        ok = bool(cited_ok and kinds_ok) and (not v.screens or v.shot("S08").get("ok"))
        v.set(8, "F2008", PASS if ok else FAIL, files,
              f"답변 근거 번호 {a1['answer_nums']}가 모두 목록에 있음, 항목 메타데이터와 외부 문서({', '.join(sorted(labels - {'항목 메타데이터'}))}) 구분 표시"
              if ok else f"번호 불일치 또는 출처 구분 없음 (답 {a1['answer_nums']}, 목록 {sorted(ev_nums)}, 표시 {sorted(labels)})")


def check_chunks(v: Verifier) -> None:
    r1 = v.run(["scripts.build_index", "--rechunk", "--chunk-size", "500", "--chunk-overlap", "100"], timeout=3600)
    r2 = v.run(["scripts.build_index", "--rechunk"], timeout=3600)     # 원래 설정으로 되돌림
    pat = r"전체 (\d+)개 \(chunk_size=(\d+), overlap=(\d+)\)"
    m1, m2 = re.search(pat, r1.out), re.search(pat, r2.out)
    files = v.save("05_F2005_청크설정_재색인", [r1, r2],
                   tail=f"[비교] 500/100 → {m1.group(1) if m1 else '?'}청크, 설정값(config/rag_settings.json) → "
                        f"{m2.group(1) if m2 else '?'}청크")
    if not (m1 and m2):
        v.set(5, "F2005", UNK, files, "재색인 출력 해석 불가 (문서 청크가 없으면 질문을 먼저 해 문서를 모으세요)")
        return
    ok = m1.group(1) != m2.group(1)
    v.set(5, "F2005", PASS if ok else FAIL, files,
          f"chunk 500/100 → {m1.group(1)}청크, {m2.group(2)}/{m2.group(3)} → {m2.group(1)}청크 (원래 설정으로 복구)"
          if ok else "청크 수가 같음 (색인된 문서가 너무 짧을 수 있음)")


def check_search(v: Verifier) -> None:
    base = ["scripts.ask", "--disease", v.disease, "--no-collect"]
    r3 = v.run(base + ["--top-k", "3", v.question])
    r8 = v.run(base + ["--top-k", "8", v.question])
    e3, e8 = parse_ask(r3.out)["evidence"], parse_ask(r8.out)["evidence"]
    files = v.save("06_F2006_TopK_근거목록비교", [r3, r8], tail=f"[비교] top_k=3 → 근거 {len(e3)}개, top_k=8 → 근거 {len(e8)}개")
    if r3.code or r8.code:
        v.set(6, "F2006", UNK, files, "ask 실행 실패")
    else:
        ok = [x[2] for x in e3] != [x[2] for x in e8]
        v.set(6, "F2006", PASS if ok else FAIL, files,
              f"같은 질문에서 top_k 3 → 근거 {len(e3)}개(메타 1 + 문서), 8 → {len(e8)}개" if ok
              else "근거 목록이 같음 (색인된 청크가 3개 이하일 수 있음)")
    # 16. F2014
    rs = v.run(base + ["--top-k", "5", "--search-type", "similarity", v.question])
    rm = v.run(base + ["--top-k", "5", "--search-type", "mmr", v.question])
    es, em = parse_ask(rs.out)["evidence"], parse_ask(rm.out)["evidence"]
    ts, tm = [f"{x[2]}" for x in es], [f"{x[2]}" for x in em]
    only_s, only_m = [t for t in ts if t not in tm], [t for t in tm if t not in ts]
    docs = lambda lst: len({t.split(" / ")[0].split(" · ")[0] for t in lst[1:]})  # noqa: E731
    files = v.save("16_F2014_유사도_MMR_비교", [rs, rm],
                   tail="[비교]\n유사도에만: " + (" | ".join(only_s) or "없음") + "\nMMR에만: " + (" | ".join(only_m) or "없음")
                        + f"\n외부 문서 종류 수: 유사도 {docs(ts)}개 · MMR {docs(tm)}개 (fetch_k={30}에서 다양성으로 선별)")
    if rs.code or rm.code:
        v.set(16, "F2014", UNK, files, "ask 실행 실패")
    else:
        v.set(16, "F2014", PASS, files,
              f"두 방식 근거 목록 나란히 비교: 서로 다른 근거 {len(only_s)}/{len(only_m)}개, 문서 다양성 {docs(ts)}→{docs(tm)}"
              + ("" if only_s or only_m else " (이번 질문에서는 결과가 같음)"))


def check_unknown(v: Verifier) -> None:
    r = v.run(["scripts.ask", "--disease", "홍역", "--policy", "when_needed", "홍역 치료제 가격은 얼마인가요?"])
    a = parse_ask(r.out)
    files = v.save("09_F2007_F2008_없는내용_질문", [r]) + v.shot_files("S09")
    if r.code:
        v.set(9, "F2007 · F2008", UNK, files, "ask 실행 실패")
        return
    abstain = "확인되지 않" in a["answer"]
    no_cite = a["cited"].startswith("없음")
    screen = v.shot("S09")
    ok = abstain and (not v.screens or screen.get("ok"))
    v.set(9, "F2007 · F2008", PASS if ok else FAIL, files,
          ("'제공된 문서에서 확인되지 않습니다'로 답함" + (" · 인용 근거 없음 → 화면에 '인용된 근거가 없습니다' 안내" if no_cite
                                                          else " · 일부 근거(급수 등)를 인용"))
          if ok else "문서에 없는 질문에 추측해 답함 → 프롬프트 3 규칙 3 확인 필요")


def check_screens_only(v: Verifier) -> None:
    # 10. F2009
    s10a, s10b = v.shot("S10a"), v.shot("S10b")
    files = v.shot_files("S10a", "S10b")
    if not v.screens:
        v.set(10, "F2009", UNK, files, "화면 캡처를 생략함 (--no-screens). 자동 테스트 tests/test_ui.py가 분리를 확인")
    else:
        ok = s10a.get("ok") and s10b.get("ok")
        v.set(10, "F2009", PASS if ok else FAIL, files,
              "홍역 대화 후 수두 질문 → 수두 대화만, 요약의 '홍역' 버튼 → 홍역 대화만 (섞이지 않음)" if ok
              else f"{s10a.get('note', '')} / {s10b.get('note', '')}")
    # 13. F2012
    ids = ["S13a", "S13b", "S13c", "S13d"]
    files = v.shot_files(*ids)
    if not v.screens:
        v.set(13, "F2012", UNK, files, "화면 캡처를 생략함 (--no-screens)")
    else:
        prog = v.shot("S13a").get("ok") or v.shot("S13b").get("ok")
        ok = prog and v.shot("S13c").get("ok") and v.shot("S13d").get("ok")
        v.set(13, "F2012", PASS if ok else FAIL, files,
              "처리 중 진행 상태·완료 후 수집 문서 제목, 색인 없음 안내(build_index), 키 없음 안내(변수 이름만)" if ok
              else " / ".join(f"{i}: {v.shot(i).get('note', '캡처 없음')}" for i in ids if not v.shot(i).get("ok")))
    # 14. F2012 면책
    files = v.shot_files("S14")
    from service.runtime import DISCLAIMER
    if not v.screens:
        v.set(14, "F2012", UNK, files, "화면 캡처를 생략함")
    else:
        ok = v.shot("S14").get("ok")
        v.set(14, "F2012", PASS if ok else FAIL, files,
              f"건강 도메인: 답변 화면·홈에 면책 안내 표시 (\"{DISCLAIMER[:40]}…\")" if ok else "면책 안내를 찾지 못함")


def check_explore(v: Verifier) -> None:
    r = v.run(["scripts.explore_check"])
    files = v.save("11_F2011_조건탐색_결과대조", [r])
    m = re.search(r"어긋난 항목 (\d+)건 / 전체 (\d+)건", r.out)
    if not m:
        v.set(11, "F2011", UNK, files, f"실행 실패 (종료 코드 {r.code})")
        return
    bad, total = int(m.group(1)), int(m.group(2))
    ok = bad == 0 and total > 0
    v.set(11, "F2011", PASS if ok else FAIL, files,
          f"조건 4개 · 결과 {total}건을 급수·신고 범위 데이터와 하나씩 대조해 어긋난 항목 0건 "
          "(데이터에 없는 조건 '모기·호흡기'는 결과의 이유를 사람이 확인)" if ok else f"조건과 어긋난 항목 {bad}건 / {total}건")


def check_travel(v: Verifier) -> None:
    r = v.run(["scripts.travel_check"])
    files = v.save("12_F2010_여행전안내_근거", [r]) + v.shot_files("S12")
    if "country_watch.json이 없습니다" in r.out:
        v.set(12, "F2010", UNK, files, "검역관리지역 정보 미수집 → python -m scripts.fetch_quarantine 후 build_index")
        return
    ok = r.code == 0 and (not v.screens or v.shot("S12").get("ok"))
    v.set(12, "F2010", PASS if ok else FAIL, files,
          "여행 전 안내(나라 팝업): 검역관리지역 API 문서·항목 메타데이터·위키 청크를 근거로 [근거 n] 표시" if ok
          else "특화 기능 답에 근거가 없거나 화면 확인 실패")


def check_rag_compare(v: Verifier) -> None:
    args = ["scripts.compare_rag"] + (["--offline"] if v.offline else [])
    r = v.run(args, timeout=3600)
    doc = paths.REPORTS / "rag_comparison_offline.md" if v.offline else paths.ROOT / "docs" / "RAG_COMPARISON.md"
    files = v.save("17_F2015_RAG전후비교_실행", [r])
    if doc.exists():
        shutil.copy(doc, v.ev / "17_F2015_RAG_COMPARISON.md")
        files.append("17_F2015_RAG_COMPARISON.md")
    n = re.search(r"대표 질문 (\d+)개", r.out)
    summ = doc.read_text(encoding="utf-8") if doc.exists() else ""
    acc = re.search(r"사실 정확성 \(정답 수\) \| (\d+)/(\d+) \| (\d+)/(\d+)", summ)
    cite = re.search(r"근거 제시 \(답에 \[근거 n\]\) \| (\d+)/\d+ \| (\d+)/\d+", summ)
    if r.code or not (n and acc):
        v.set(17, "F2015", UNK, files, "비교 실행 실패 (사용 한도 등) → 다시 실행")
    else:
        ok = int(n.group(1)) >= 5
        v.set(17, "F2015", PASS if ok else FAIL, files,
              f"대표 질문 {n.group(1)}개 · 사실 정확성 RAG 없이 {acc.group(1)}/{acc.group(2)} → RAG {acc.group(3)}/{acc.group(4)}"
              f" · 근거 제시 {cite.group(1) if cite else '?'} → {cite.group(2) if cite else '?'} · 모름 표명 비교 포함")
    real_doc = paths.ROOT / "docs" / "RAG_COMPARISON.md"
    if v.offline:
        v.set(18, "F2015", UNK, ["17_F2015_RAG_COMPARISON.md"], "오프라인 모의 결과는 제출 문서가 아님 → 실제 모드로 다시 실행")
    elif real_doc.exists() and "오프라인 모의" not in real_doc.read_text(encoding="utf-8"):
        v.set(18, "F2015", PASS, ["docs/RAG_COMPARISON.md"], "비교 결과 문서를 docs/RAG_COMPARISON.md로 제출물에 포함")
    else:
        v.set(18, "F2015", FAIL, [], "docs/RAG_COMPARISON.md 없음")


def check_sources(v: Verifier) -> None:
    after = v.cache.get("inspect_after") or v.run(["scripts.inspect_index"], "inspect_after")
    m = re.search(r"출처 종류별 청크: (\{.*?\})", after.out)
    kinds = json.loads(m.group(1).replace("'", '"')) if m else {}
    a1 = parse_ask(v.cache["ask1"].out) if "ask1" in v.cache else {"evidence": []}
    labels = sorted({lab for _, lab, _ in a1["evidence"]})
    files = v.save("19_F2016_출처종류_색인과_근거표시", [after], tail=f"[색인] 출처 종류별 청크 {kinds}\n[근거 표시] {labels}") \
        + v.shot_files("S19", "S12")
    types = [k for k in kinds if kinds[k]]
    ok = len(types) >= 2 and (not v.screens or v.shot("S19").get("ok"))
    v.set(19, "F2016", PASS if ok else FAIL, files,
          f"disease_docs에 출처 종류 {types} 함께 색인(위키=글자 수 청킹, 공공데이터=레코드 청킹), 근거 목록에 종류 배지"
          if ok else f"출처 종류가 {types}뿐 → fetch_quarantine·build_age_stats 후 build_index 실행")


def check_speed(v: Verifier) -> None:
    did, q1, q2 = ("홍역", "홍역의 잠복기는 얼마나 되나요?", "홍역 예방접종은 언제 하나요?") if v.offline else \
        ("레지오넬라증", "레지오넬라증은 어떻게 감염되나요?", "레지오넬라증의 주요 증상은 무엇인가요?")
    base = ["scripts.ask", "--disease", did, "--policy", "background"]
    r1, r2 = v.run(base + [q1]), v.run(base + [q2])
    t1, t2 = parse_ask(r1.out)["sec"], parse_ask(r2.out)["sec"]
    perf = paths.ROOT / "docs" / "PERFORMANCE.md"
    files = v.save("20_NF2001_처리시간_첫질문_두번째", [r1, r2],
                   tail=f"[측정] 앱 기본 설정(배경 수집) · 첫 질문 {t1}초, 두 번째 질문 {t2}초\n"
                        f"[참고] 전체 측정 기록: docs/PERFORMANCE.md (python -m scripts.bench_chat --quick)")
    if t1 is None or t2 is None:
        v.set(20, "NF2001", UNK, files, "처리 시간 측정 실패 (사용 한도 등)")
    else:
        ok = t1 <= 20 and t2 <= 10
        v.set(20, "NF2001", PASS if ok else FAIL, files,
              f"첫 질문 {t1}s(문서 수집 포함), 두 번째 {t2}s · 답변은 스트리밍이라 첫 글자는 더 빨리 보임 (기준: 첫 20s·이후 10s 이내)"
              + (" · 상세 측정 docs/PERFORMANCE.md" if perf.exists() else ""))


def check_security(v: Verifier) -> None:
    r = v.run(["scripts.scan_secrets", str(v.out)])
    files = v.save("21_NF2003_NF2004_키검사", [r]) + v.shot_files("S21", "S13d")
    base = "저장소·제출 폴더에 키 값·키 모양 없음, .env는 .gitignore"
    if r.code != 0:
        v.set(21, "NF2003 · NF2004", FAIL, files, r.out.split("[결과]")[-1].strip()[:200])
    elif not v.screens:
        v.set(21, "NF2003 · NF2004", UNK, files, base + " / 오류 화면 확인은 캡처를 생략해 미확인")
    elif v.shot("S21").get("ok"):
        v.set(21, "NF2003 · NF2004", PASS, files, base + ", 오류 화면(틀린 키)·키 없음 화면에 키·프롬프트·추적 정보 없음")
    else:
        v.set(21, "NF2003 · NF2004", FAIL, files, "오류 화면: " + v.shot("S21").get("note", "캡처 없음"))


def check_modules(v: Verifier) -> None:
    roles = {"data_prep": "수집 (위키·공공데이터 API·통계 파일, 출처 등록부)", "vectorstore": "색인·검색 (Chroma 두 컬렉션)",
             "rag": "검색·생성 (LangGraph retrieve→generate, 프롬프트)", "service": "도메인 기능 (조건 탐색·여행 안내·통계)",
             "ui": "화면 (Streamlit)", "scripts": "실행 스크립트 (색인·검증·측정)", "tests": "자동 테스트", "config": "설정"}
    lines = ["[모듈 구성] 폴더 · 역할 · 파일"]
    for d, role in roles.items():
        fs = sorted(p.relative_to(paths.ROOT).as_posix() for p in (paths.ROOT / d).rglob("*.py" if d != "config" else "*.json")
                    if "__pycache__" not in p.parts)
        lines.append(f"\n{d}/  — {role}")
        for f in fs:
            first = ""
            if f.endswith(".py"):
                doc = re.match(r'\s*(?:from __future__.*\n)?\s*"""(.+?)(?:\n|""")', (paths.ROOT / f).read_text(encoding="utf-8"))
                first = doc.group(1).strip() if doc else ""
            lines.append(f"   {f}" + (f"  — {first[:70]}" if first else ""))
    txt = v.ev / "22_NF2005_모듈구성.txt"
    txt.write_text("\n".join(lines), encoding="utf-8")
    files = ([f"22_NF2005_모듈구성.png"] if text_to_png(txt, v.ev / "22_NF2005_모듈구성.png") else []) + [txt.name]
    v.set(22, "NF2005", PASS, files, "수집 data_prep · 색인/검색 vectorstore · 생성 rag · 도메인 service · UI ui 로 분리")


def check_etiquette(v: Verifier) -> None:
    r = v.run(["unittest", "-v", "tests.test_stage1.WikiClientTest.test_requests_follow_etiquette",
               "tests.test_data_prep.HttpTest.test_retry_then_ok",
               "tests.test_data_prep.HttpTest.test_4xx_masks_key"])
    excerpts = []
    for f, pats in {"data_prep/wiki.py": [r"min_interval", r"max_retries", r"user_agent", r"maxlag"],
                    "data_prep/http_client.py": [r"min_interval", r"max_retries", r"User-Agent", r"mask"],
                    "config/rag_settings.json": [r"wiki_min_interval_sec", r"wiki_max_retries"],
                    ".env.example": [r"WIKI_USER_AGENT"]}.items():
        text = (paths.ROOT / f).read_text(encoding="utf-8").splitlines()
        hits = [f"  {f}:{i + 1}: {l.strip()}" for i, l in enumerate(text) if any(re.search(p, l) for p in pats)]
        excerpts += [f"[{f}]"] + hits[:12]
    files = v.save("23_NF2006_재시도_간격_UserAgent", [r], head="\n".join(excerpts))
    ok = r.code == 0 and "OK" in r.out
    v.set(23, "NF2006", PASS if ok else FAIL, files,
          "위키: 요청 간 최소 0.5s·재시도 3회·maxlag=5·연락처 든 User-Agent 필수 / 공공데이터 API: 간격·재시도·키 마스킹 (코드+테스트)"
          if ok else "관련 테스트 실패 또는 테스트 이름 변경")


def check_license(v: Verifier) -> None:
    readme = (paths.ROOT / "README.md").read_text(encoding="utf-8")
    sec = readme[readme.find("## 6. 출처와 이용 조건"):readme.find("## 7.")]
    r = v.run(["unittest", "-v", "tests.test_sources"])
    files = v.save("24_NF2007_출처_이용조건", [r], head="[README.md 6절]\n" + sec)
    ok = bool(sec) and r.code == 0
    v.set(24, "NF2007", PASS if ok else FAIL, files,
          "README 6절 표 + config/sources.json(이용 조건·확인일), 화면 캡션에 출처·이용 조건" if ok else "README 6절 또는 등록부 검사 실패")


def run_tests(v: Verifier) -> dict:
    env = {k: val for k, val in v.env.items() if not k.startswith("KDCA_")}
    t0 = time.perf_counter()
    p = subprocess.run([PY, "-m", "unittest", "discover", "-s", "tests", "-t", ".", "-v"], cwd=paths.ROOT, env=env,
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    sec = time.perf_counter() - t0
    rows = re.findall(r"^(test\w+) \((tests\.[\w.]+)\)\n?(.*?) \.\.\. (ok|FAIL|ERROR|skipped.*)$", p.stderr, flags=re.M)
    total = re.search(r"Ran (\d+) tests", p.stderr)
    return {"rows": rows, "ok": p.returncode == 0, "total": int(total.group(1)) if total else len(rows), "sec": round(sec, 1),
            "tail": p.stderr[-1500:]}


# ---- 결과 문서·xlsx ------------------------------------------------------------------------------
REQS = [
    ("F2001", "항목 메타데이터 로드·색인 (133건, 1건=1청크)", "data_prep/diseases.py, scripts/build_index.py", [1]),
    ("F2002", "재색인 멱등성 (청크 ID 고정 + upsert)", "vectorstore/store.py", [2]),
    ("F2003", "프롬프트 1 검색어 추출", "rag/prompts.py, rag/graph.py extract_queries", [3]),
    ("F2004", "프롬프트 2 후보 선별 + 수집 캐시", "rag/graph.py make_selector, data_prep/collector.py", [3, 4]),
    ("F2005", "청크 크기·중복 길이 설정", "config/rag_settings.json, vectorstore/store.py chunk_page", [5]),
    ("F2006", "Top-k 검색 (감염병 범위 한정)", "vectorstore/store.py search_docs", [6]),
    ("F2007", "LangGraph retrieve → generate, 근거 기반 답변", "rag/graph.py, rag/prompts.py", [7, 9]),
    ("F2008", "근거 번호·출처 표시, 인용 없음 안내", "rag/graph.py build_evidence, ui/chat_core.py", [8, 9]),
    ("F2009", "항목 선택·항목별 대화 기록", "ui/views/chat.py", [10]),
    ("F2010", "도메인 특화: 여행 전 안내(근거 포함)·나라별·연령별", "service/travel.py, ui/views/home.py", [12]),
    ("F2011", "조건 기반 항목 탐색", "service/explore.py, ui/views/chat.py", [11]),
    ("F2012", "진행 상태·색인/키 누락 안내·면책", "ui/app.py, ui/chat_core.py", [13, 14]),
    ("F2013", "HTML·CSS·JS 프론트엔드 (심화, 선택 안 함)", "-", [15]),
    ("F2014", "MMR 검색 (심화)", "vectorstore/store.py, config search_type", [16]),
    ("F2015", "RAG 전후 품질 비교 (심화)", "scripts/compare_rag.py, docs/RAG_COMPARISON.md", [17, 18]),
    ("F2016", "여러 종류 문서 출처 결합 (심화)", "data_prep/public_docs.py", [19]),
    ("NF2001", "응답 시간", "docs/PERFORMANCE.md, rag/metrics.py", [20]),
    ("NF2002", "수집 캐시 (같은 검색어 재수집 없음)", "data_prep/collector.py", [4]),
    ("NF2003", "API 키 관리 (.env, 저장소 미포함)", ".env.example, .gitignore", [21]),
    ("NF2004", "화면에 키·프롬프트·추적 정보 노출 없음", "ui/chat_core.py, service/runtime.py setup_logging", [21]),
    ("NF2005", "모듈 분리", "data_prep / vectorstore / rag / service / ui", [22]),
    ("NF2006", "외부 호출 예절 (간격·재시도·User-Agent)", "data_prep/wiki.py, data_prep/http_client.py", [23]),
    ("NF2007", "출처·이용 조건 기록", "README.md 6절, config/sources.json", [24]),
]
TEST_MAP = {"test_stage1": "F2001~F2008·F2011·NF2006", "test_ui": "F2009~F2012 (화면)", "test_speed": "NF2001·F2004",
            "test_public_docs": "F2010·F2016", "test_sources": "NF2007", "test_quarantine": "F2010 (검역 데이터)",
            "test_age_stats": "F2010 (연령 통계)", "test_data_prep": "F2001·NF2006", "test_verify": "검증 도구·F2011·F2015"}


def write_docs(v: Verifier, tests: dict) -> list[Path]:
    vd = v.out / "검증결과"
    vd.mkdir(parents=True, exist_ok=True)
    mode = " (오프라인 모의 실행 — 제출용 아님)" if v.offline else ""
    lines = [f"# 요구사항 대조표{mode}", "", f"생성: {datetime.now():%Y-%m-%d %H:%M} · `python -m scripts.verify_checklist`", "",
             "| 요구사항 | 내용 | 구현 위치 | 체크리스트 | 결과 | 증빙 |", "| --- | --- | --- | --- | --- | --- |"]
    for rid, desc, where, nos in REQS:
        rs = [v.results[n] for n in nos if n in v.results]
        status = NA if rs and all(r.status == NA for r in rs) else \
            (PASS if rs and all(r.status == PASS for r in rs) else (FAIL if any(r.status == FAIL for r in rs) else UNK))
        ev = ", ".join(f for r in rs for f in r.files[:2])
        lines.append(f"| {rid} | {desc} | {where} | {', '.join(map(str, nos))} | {status} | {ev} |")
    (vd / "요구사항_대조표.md").write_text("\n".join(lines) + "\n", encoding="utf-8")

    t = [f"# 테스트 결과표{mode}", "", f"`python -m unittest discover -s tests -t . -v` · {tests['total']}개 · "
         f"{'모두 통과' if tests['ok'] else '실패 있음'} · {tests['sec']}s (키·네트워크 없이 가짜 LLM·가짜 위키)", "",
         "| # | 모듈 | 관련 요구사항 | 테스트 | 설명 | 결과 |", "| --- | --- | --- | --- | --- | --- |"]
    for i, (name, where, doc, res) in enumerate(tests["rows"], start=1):
        mod = where.split(".")[1]
        t.append(f"| {i} | {mod} | {TEST_MAP.get(mod, '')} | {name} | {doc.strip()[:70]} | {'통과' if res == 'ok' else res} |")
    if not tests["ok"]:
        t += ["", "```", tests["tail"], "```"]
    (vd / "테스트_결과표.md").write_text("\n".join(t) + "\n", encoding="utf-8")

    cnt = {s: sum(r.status == s for r in v.results.values()) for s in (PASS, FAIL, UNK, NA)}
    s = [f"# 셀프 체크리스트 검증 요약{mode}", "", f"- 실행: {datetime.now():%Y-%m-%d %H:%M}",
         f"- 결과: 통과 {cnt[PASS]} · 실패 {cnt[FAIL]} · 미확인 {cnt[UNK]} · 해당없음 {cnt[NA]} (전체 {len(v.results)})",
         f"- 자동 테스트: {tests['total']}개 {'모두 통과' if tests['ok'] else '실패 있음'}", "",
         "| 번호 | 요구사항 | 결과 | 증빙 | 비고 |", "| --- | --- | --- | --- | --- |"]
    for n in sorted(v.results):
        r = v.results[n]
        s.append(f"| {n} | {r.req} | {r.status} | {', '.join(r.files)} | {r.note} |")
    (vd / "검증_요약.md").write_text("\n".join(s) + "\n", encoding="utf-8")
    return [vd / "요구사항_대조표.md", vd / "테스트_결과표.md", vd / "검증_요약.md"]


def fill_xlsx(src: Path, dst: Path, v: Verifier, author: str) -> None:
    import openpyxl
    wb = openpyxl.load_workbook(src)
    ws = wb["체크리스트"] if "체크리스트" in wb.sheetnames else wb.worksheets[0]
    ws["C3"] = "셀프"
    if author:
        ws["C4"] = author
    ws["C6"] = datetime.now().strftime("%Y-%m-%d %H:%M") + (" (오프라인 모의 — 제출용 아님)" if v.offline else "")
    for row in ws.iter_rows(min_row=9, max_row=40):
        no = row[2].value              # C열 번호
        if isinstance(no, int) and no in v.results:
            r = v.results[no]
            ws.cell(row=row[0].row, column=7, value=r.status)
            ws.cell(row=row[0].row, column=8, value=", ".join(r.files) or None)
            ws.cell(row=row[0].row, column=9, value=r.note or None)
            for col in (8, 9):
                ws.cell(row=row[0].row, column=col).alignment = openpyxl.styles.Alignment(wrap_text=True, vertical="top")
    dst.parent.mkdir(parents=True, exist_ok=True)
    wb.save(dst)


# ---- 실행 -----------------------------------------------------------------------------------------
def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="셀프 체크리스트 검증·증빙 생성")
    ap.add_argument("--xlsx", help="체크리스트 양식 (없으면 결과 문서만)")
    ap.add_argument("--out", help="결과 폴더 (기본 submission, --offline이면 reports/offline_check)")
    ap.add_argument("--author", default="", help="앱 작성자 칸")
    ap.add_argument("--no-screens", action="store_true", help="화면 캡처 생략")
    ap.add_argument("--offline", action="store_true", help="키 없이 가짜 모델로 도구만 시험 (결과는 제출용 아님)")
    ap.add_argument("--only", help="항목 번호 (예: 1,2,3). 나머지 항목은 지난 결과(검증결과/results.json)를 그대로 쓴다")
    ap.add_argument("--fill-only", action="store_true", help="다시 실행하지 않고 지난 결과로 문서·xlsx만 만든다")
    ap.add_argument("--import-log", action="append", default=[],
                    help="지난 실행의 콘솔 출력을 저장한 파일 (여러 번 지정 가능, 뒤 파일이 우선)")
    args = ap.parse_args(argv)
    xlsx = resolve_xlsx(args.xlsx)
    if args.xlsx and xlsx is None:
        return 2

    out = Path(args.out) if args.out else (paths.REPORTS / "offline_check" if args.offline else paths.ROOT / "submission")
    out = out if out.is_absolute() else paths.ROOT / out
    env = {**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"}
    if args.offline:
        data = out / "offline_data"
        shutil.rmtree(data, ignore_errors=True)
        env.update(KDCA_OFFLINE_DEMO="1", KDCA_DATA_DIR=str(data), OPENAI_API_KEY="offline-demo",
                   WIKI_USER_AGENT="kdca-rag-offline (contact: offline@example.com)")
        subprocess.run([PY, "-m", "scripts.offline_env", "--prepare"], cwd=paths.ROOT, env=env, check=True)
    elif not args.fill_only:      # 다시 실행하지 않으면 키가 필요 없다
        from rag.llm import missing_keys
        if missing_keys():
            print("필요한 환경변수가 없습니다:", ", ".join(missing_keys()), "→ .env 확인 (키 없이 시험하려면 --offline)")
            return 2
    screens = not args.no_screens
    if screens:
        try:
            import playwright  # noqa: F401
        except ImportError:
            print("[!] playwright가 없어 화면 캡처를 생략합니다: pip install playwright && python -m playwright install chromium")
            screens = False
    v = Verifier(out, args.offline, screens, env)
    saved = load_saved(v) if (args.only or args.fill_only or args.import_log) else {}
    for log_file in args.import_log:
        n = import_log(v, Path(log_file))
        saved = saved or ({"results": True} if n else {})
    if args.fill_only:
        if not saved:
            print(f"지난 결과가 없습니다: {out / '검증결과' / 'results.json'}")
            return 2
        print("\n== 자동 테스트 (키 없이, 다시 실행)")
        finish(v, run_tests(v), xlsx, args.author)
        return 0
    only = {int(x) for x in args.only.split(",")} if args.only else None
    want = (lambda *nos: not only or any(n in only for n in nos))
    print(f"[검증] {'오프라인 모의' if args.offline else '실제'} 실행 → {out}")

    steps = [((1, 2, 7), check_index), ((3, 4, 8), check_collect), ((5,), check_chunks), ((6, 16), check_search),
             ((9,), check_unknown), ((11,), check_explore), ((12,), check_travel), ((17, 18), check_rag_compare),
             ((19,), check_sources), ((20,), check_speed), ((22,), check_modules), ((23,), check_etiquette),
             ((24,), check_license)]
    # 화면 캡처는 색인을 만든 뒤, 화면 결과를 쓰는 항목(8 등)보다 먼저
    if want(1, 2, 7):
        print("\n== 항목 1, 2, 7")
        check_index(v)
    if screens and want(8, 9, 10, 12, 13, 14, 19, 21):
        print("\n== 화면 캡처 (scripts/capture_screens.py)")
        r = v.run(["scripts.capture_screens", "--out", str(v.ev)], timeout=3600,
                  extra_env={"KDCA_OFFLINE_FAIL": ""})
        try:
            v.shots = {s["id"]: s for s in json.loads((v.ev / "screens.json").read_text(encoding="utf-8"))}
        except (OSError, ValueError):
            print("   화면 캡처 실패:", r.out[-800:])
    for nos, fn in steps[1:]:
        if want(*nos):
            print(f"\n== 항목 {', '.join(map(str, nos))}")
            try:
                fn(v)
            except Exception as exc:  # noqa: BLE001 - 한 항목이 실패해도 나머지는 계속
                for n in nos:
                    v.set(n, "", UNK, [], f"검증 도중 오류: {type(exc).__name__}")
    if want(10, 13, 14):
        check_screens_only(v)
    if want(21):
        check_security(v)
    if want(15):
        v.set(15, "F2013", NA, [], "심화 F2013(HTML·CSS·JS 프론트엔드)은 선택하지 않음 — 화면은 Streamlit으로 구현")

    print("\n== 자동 테스트")
    tests = run_tests(v)
    print(f"   {tests['total']}개 · {'모두 통과' if tests['ok'] else '실패 있음'}")
    finish(v, tests, xlsx, args.author)
    return 0


def resolve_xlsx(arg: str | None) -> Path | None:
    """체크리스트 경로 확인 (검증을 다 돌린 뒤 파일이 없어 실패하지 않게 먼저 확인)."""
    if not arg:
        return None
    p = Path(arg)
    if p.exists():
        return p.resolve()
    found = sorted({q.resolve() for d in (Path.cwd(), paths.ROOT, paths.ROOT / "submission")
                    for pat in ("*체크리스트*.xlsx", "*checklist*.xlsx") for q in d.glob(pat)
                    if "_결과" not in q.name and not q.name.startswith("~$")})
    if len(found) == 1:
        print(f"[!] '{arg}'이(가) 없어 대신 찾은 파일을 씁니다: {found[0]}")
        return found[0]
    print(f"[x] 체크리스트 파일이 없습니다: {p.resolve()}")
    if found:
        print("    후보:", *found, sep="\n      ")
    print("    --xlsx에 실제 파일 경로를 주세요 (예: --xlsx \"C:/Users/SSAFY/Downloads/2회차_셀프체크리스트.xlsx\")")
    return None


def results_path(v: Verifier) -> Path:
    return v.out / "검증결과" / "results.json"


ITEM_REQ = {1: "F2001", 2: "F2002", 3: "F2003 · F2004", 4: "F2004 · NF2002", 5: "F2005", 6: "F2006", 7: "F2007",
            8: "F2008", 9: "F2007 · F2008", 10: "F2009", 11: "F2011", 12: "F2010", 13: "F2012", 14: "F2012",
            15: "F2013", 16: "F2014", 17: "F2015", 18: "F2015", 19: "F2016", 20: "NF2001", 21: "NF2003 · NF2004",
            22: "NF2005", 23: "NF2006", 24: "NF2007"}
ITEM_SHOTS = {8: ["S08"], 9: ["S09"], 10: ["S10a", "S10b"], 12: ["S12"], 13: ["S13a", "S13b", "S13c", "S13d"],
              14: ["S14"], 19: ["S19", "S12"], 21: ["S21", "S13d"]}


def import_log(v: Verifier, path: Path) -> int:
    """콘솔 출력의 '[통과]  3 F2003 · F2004 · 비고' 줄에서 결과를 복원. 증빙 파일은 evidence 폴더에서 다시 찾는다."""
    if not path.exists():
        print(f"[!] 로그 파일이 없습니다: {path}")
        return 0
    try:
        v.shots = v.shots or {s["id"]: s for s in json.loads((v.ev / "screens.json").read_text(encoding="utf-8"))}
    except (OSError, ValueError):
        pass
    text = path.read_bytes().decode("utf-8", errors="replace")
    n = 0
    for m in re.finditer(r"^\s*\[(통과|실패|미확인|해당없음)\]\s+(\d+) (.*)$", text, flags=re.M):
        no, rest = int(m.group(2)), m.group(3).strip()
        if no not in ITEM_REQ:       # 25~27은 다시 계산
            continue
        req = ITEM_REQ[no]
        note = rest[len(req):].lstrip(" ·") if rest.startswith(req) else rest
        files = sorted((f.name for f in v.ev.glob(f"{no:02d}_*")), key=lambda f: (not f.endswith(".png"), f))
        files += v.shot_files(*ITEM_SHOTS.get(no, []))
        if no == 18 and m.group(1) == PASS:
            files = ["docs/RAG_COMPARISON.md"]
        v.results[no] = Result(no, req, m.group(1), list(dict.fromkeys(files)), note)
        n += 1
    print(f"[검증] {path.name}에서 결과 {n}줄을 복원")
    return n


def load_saved(v: Verifier) -> dict:
    p = results_path(v)
    summary = v.out / "검증결과" / "검증_요약.md"
    if p.exists():
        data = json.loads(p.read_text(encoding="utf-8"))
        v.results = {int(k): Result(**r) for k, r in data.get("results", {}).items()}
    elif summary.exists():       # results.json이 생기기 전 버전이 남긴 요약 표에서 복원
        data = {}
        for m in re.finditer(r"^\| (\d+) \| (.*?) \| (통과|실패|미확인|해당없음) \| (.*?) \| (.*) \|$",
                             summary.read_text(encoding="utf-8"), flags=re.M):
            files = [f.strip() for f in m.group(4).split(",") if f.strip()]
            v.results[int(m.group(1))] = Result(int(m.group(1)), m.group(2), m.group(3), files, m.group(5))
        p = summary
    else:
        return {}
    try:
        v.shots = {s["id"]: s for s in json.loads((v.ev / "screens.json").read_text(encoding="utf-8"))}
    except (OSError, ValueError):
        pass
    print(f"[검증] 지난 결과 {len(v.results)}항목을 불러옴 ({p})")
    return data or {"results": True}


def finish(v: Verifier, tests: dict, xlsx: Path | None, author: str) -> None:
    """25~27 제출 전 점검을 다시 계산하고 문서·xlsx·results.json을 쓴다."""
    for n in (25, 26, 27):
        v.results.pop(n, None)
    out = v.out
    # 25~27 제출 전 점검
    req = [v.results.get(n) for n in range(1, 15) if n != 15]
    adv = [v.results.get(n) for n in (16, 17, 18, 19)]
    if all(r and r.status == PASS for r in req) and all(r and r.status == PASS for r in adv):
        v.set(25, "필수", PASS, ["검증결과/검증_요약.md", "17_F2015_RAG_COMPARISON.md"],
              "1~14 모두 통과, 심화 F2014·F2015·F2016을 필수 결과(유사도 검색·RAG 없는 답·위키 단일 출처)와 비교")
    else:
        left = [str(r.no) for r in req + adv if r and r.status != PASS] + [str(n) for n, r in zip(list(range(1, 15)) + [16, 17, 18, 19], req + adv) if r is None and n != 15]
        v.set(25, "필수", UNK, ["검증결과/검증_요약.md"], f"통과하지 않은 항목: {', '.join(sorted(set(left), key=int))}")
    shots_needed = ["S08", "S09", "S10a", "S10b", "S12", "S13b", "S13c", "S13d", "S14", "S19", "S21"]
    have = [s for s in shots_needed if v.shot(s).get("file") and (v.ev / v.shot(s)["file"]).exists()]
    v.set(26, "필수", PASS if len(have) == len(shots_needed) else UNK, ["evidence/"],
          f"화면 캡처 {len(have)}/{len(shots_needed)}개 + 실행 출력 캡처(NN_*.png) 저장" if len(have) == len(shots_needed)
          else f"빠진 화면 캡처: {', '.join(s for s in shots_needed if s not in have)}")
    docs_written = write_docs(v, tests)
    deliver = {"요구사항 정의서": paths.ROOT / "docs" / "REQUIREMENTS.md", "RAG 파이프라인 설계문서": paths.ROOT / "docs" / "RAG_DESIGN.md",
               "README.md": paths.ROOT / "README.md", "요구사항 대조표": docs_written[0], "테스트 결과표": docs_written[1]}
    miss = [k for k, p in deliver.items() if not p.exists()]
    peer = list(out.glob("*상호*.xlsx"))
    note = ("요구사항 정의서·설계문서·소스·README·대조표·테스트 결과표·셀프 체크리스트 포함"
            + ("" if peer else " / 상호 체크리스트는 옆 교육생 점검 후 submission/에 추가해야 함"))
    v.set(27, "필수", PASS if not miss and peer and not v.offline else UNK, [p.name for p in docs_written[:2]],
          note if not miss else f"빠진 문서: {', '.join(miss)}")
    write_docs(v, tests)      # 25~27 반영해 다시 씀
    results_path(v).write_text(json.dumps({"offline": v.offline, "tests": tests,
                                           "results": {n: r.__dict__ for n, r in sorted(v.results.items())}},
                                          ensure_ascii=False, indent=2), encoding="utf-8")
    if xlsx:
        fill_xlsx(xlsx, out / "셀프체크리스트_결과.xlsx", v, author)
        print(f"\n[저장] {out / '셀프체크리스트_결과.xlsx'}")
    else:
        print("\n[!] --xlsx를 주지 않아 체크리스트 사본은 만들지 않았습니다 (나중에 --fill-only --xlsx <파일>)")
    print(f"[저장] {out / '검증결과'}, 증빙 {v.ev}")
    cnt = {s: sum(r.status == s for r in v.results.values()) for s in (PASS, FAIL, UNK, NA)}
    print(f"[요약] 통과 {cnt[PASS]} · 실패 {cnt[FAIL]} · 미확인 {cnt[UNK]} · 해당없음 {cnt[NA]}")


if __name__ == "__main__":
    sys.exit(main())
