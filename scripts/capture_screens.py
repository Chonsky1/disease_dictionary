"""체크리스트 화면 증빙 캡처 (Playwright로 Streamlit 앱을 띄워 실제로 조작하고 PNG로 저장).

python -m scripts.capture_screens --out submission/evidence
python -m scripts.capture_screens --out ... --only S08,S10     # 일부만

준비: pip install playwright && python -m playwright install chromium
앱은 이 스크립트가 직접 띄운다 (포트 8599~). 이미 켜 둔 앱과 겹치지 않는다.
'색인 없음'은 빈 KDCA_DATA_DIR, '키 없음'은 OPENAI_API_KEY="" , '오류 화면'은 틀린 키(오프라인은 KDCA_OFFLINE_FAIL=1)로
따로 띄운다. 같은 질문 저장 답변은 끄고 실행한다 (진행 상태가 실제로 보이게).

장면 (체크리스트 번호)
  S08  답변 + 근거 목록 (8, 19)          S13a 처리 중 진행 상태 (13)     S13b 완료 후 진행 기록·수집 문서 제목 (13)
  S09  문서에 없는 질문 (9)               S10a·S10b 감염병별 대화 분리 (10) S14  면책 안내 (14)
  S12  여행 전 안내 + 근거 (12, 19)       S19  근거 출처 종류 배지 (19)     S13c 색인 없음 안내 (13)
  S13d 키 없음 안내 (13)                  S21  오류 화면에 추적 정보 없음 (21)
화면마다 글자 검사 결과(ok/note)를 함께 돌려준다 (verify_checklist가 체크리스트에 씀).
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

from data_prep import settings as paths

PORT = 8599
VIEW = {"width": 1440, "height": 1300}
Q_MAIN = "홍역은 어떻게 전파되나요?"
Q_UNKNOWN = "홍역 치료제 가격은 얼마인가요?"
Q_OTHER = "수두는 어떻게 전파되나요?"
LEAK = [("Traceback", "추적 정보"), ('File "', "추적 정보"), ("sk-", "키 모양"), ("너는 질병관리청 자료와", "프롬프트 전문"),
        ("너는 해외여행자에게", "프롬프트 전문")]


@dataclass
class Shot:
    id: str
    file: str = ""
    ok: bool = False
    note: str = ""
    checks: dict = field(default_factory=dict)


class App:
    """Streamlit 앱을 따로 띄운다."""

    def __init__(self, port: int, env: dict, log: Path):
        self.port, self.env, self.log = port, env, log
        self.proc = None

    def __enter__(self):
        env = {**os.environ, **self.env}
        self.log.parent.mkdir(parents=True, exist_ok=True)
        self.fh = open(self.log, "w", encoding="utf-8")
        self.proc = subprocess.Popen(
            [sys.executable, "-m", "streamlit", "run", "ui/app.py", "--server.port", str(self.port),
             "--server.headless", "true", "--browser.gatherUsageStats", "false", "--server.fileWatcherType", "none"],
            cwd=paths.ROOT, env=env, stdout=self.fh, stderr=subprocess.STDOUT)
        for _ in range(120):
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{self.port}/_stcore/health", timeout=2) as r:
                    if r.status == 200:
                        return self
            except OSError:
                time.sleep(0.5)
        raise RuntimeError(f"앱이 뜨지 않습니다. 로그: {self.log}")

    def __exit__(self, *exc):
        if self.proc:
            self.proc.terminate()
            try:
                self.proc.wait(10)
            except subprocess.TimeoutExpired:
                self.proc.kill()
        self.fh.close()

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}"


def settle(page, ms: int = 1500, timeout: float = 120) -> None:
    """Streamlit 실행이 끝날 때까지 (오른쪽 위 실행 표시가 사라지고 잠시 그대로일 때)."""
    end = time.time() + timeout
    quiet = 0.0
    while time.time() < end:
        running = page.locator('[data-testid="stStatusWidget"]').count() > 0
        quiet = 0.0 if running else quiet + 0.25
        if quiet * 1000 >= ms:
            return
        page.wait_for_timeout(250)


def wait_text(page, text: str, timeout: float = 180) -> bool:
    try:
        page.get_by_text(text, exact=False).first.wait_for(timeout=timeout * 1000)
        return True
    except Exception:  # noqa: BLE001
        return False


def chat_text(page) -> str:
    loc = page.locator('[class*="st-key-chat_box"]')
    return loc.first.inner_text() if loc.count() else page.inner_text("body")


def scroll_chat_bottom(page) -> None:
    page.evaluate("""() => { document.querySelectorAll('[class*="st-key-chat_box"], [class*="st-key-chat_box"] *')
        .forEach(e => { if (e.scrollHeight > e.clientHeight) e.scrollTop = e.scrollHeight; }); }""")


def expand_last(page, label: str, want_open: bool = True) -> bool:
    """제목에 label이 들어간 마지막 접는 칸을 연다(또는 닫는다). 이미 그 상태면 그대로."""
    loc = page.locator('[data-testid="stExpander"] details', has=page.locator("summary", has_text=label))
    if not loc.count():
        return False
    det = loc.last
    if (det.get_attribute("open") is not None) != want_open:
        det.locator("summary").first.click()
        page.wait_for_timeout(600)
    return True


def ask(page, q: str) -> None:
    box = page.locator('[data-testid="stChatInputTextArea"]')
    box.wait_for(timeout=60_000)
    box.fill(q)
    box.press("Enter")


def answered(page, n: int, timeout: float = 240) -> bool:
    """대화창의 시간 표시(⏱)가 n개가 될 때까지."""
    end = time.time() + timeout
    while time.time() < end:
        if page.locator('[class*="st-key-chat_box"] .kd-timing').count() >= n or \
                page.locator('[data-testid="stAlertContentError"]').count():
            settle(page, 1200)
            return True
        page.wait_for_timeout(400)
    return False


def leak_check(text: str) -> list[str]:
    return [f"{label}('{word}')" for word, label in LEAK if word in text]


def pick_country() -> str:
    from service.countries import load_watch
    w = load_watch(paths.PROCESSED / "country_watch.json")
    if not w.loaded:
        return ""
    best = max(w.countries.values(), key=lambda c: sum(1 for x in c.diseases if x.get("disease_id")))
    return best.key if any(x.get("disease_id") for x in best.diseases) else ""


# ---- 장면 --------------------------------------------------------------------------------
def chat_flow(page, url: str, out: Path) -> list[Shot]:
    shots = {k: Shot(k) for k in ("S13a", "S13b", "S08", "S09", "S10a", "S10b", "S14")}
    page.goto(url + "/chat")
    settle(page)
    ask(page, Q_MAIN)
    # 처리 중: 상태 영역('근거를 찾는 중' 등)이 보이는 순간
    try:
        page.locator('[data-testid="stExpander"]', has_text=re.compile("근거를 찾는 중|검색어|수집|답변을 생성")).first.wait_for(timeout=30_000)
        expand_last(page, "")
        page.wait_for_timeout(300)
        page.screenshot(path=str(out / "S13a_진행상태_처리중.png"))
        shots["S13a"] = Shot("S13a", "S13a_진행상태_처리중.png", True, "처리 중 상태 영역 캡처")
    except Exception:  # noqa: BLE001 - 너무 빨리 끝나면 완료 후 기록으로 대신
        shots["S13a"].note = "처리가 빨라 처리 중 화면을 잡지 못함 → S13b(완료 후 진행 기록)로 대신"
    ok = answered(page, 1)
    if expand_last(page, "완료"):
        scroll_chat_bottom(page)
        page.screenshot(path=str(out / "S13b_진행상태_완료기록.png"))
        txt = chat_text(page)
        has_titles = bool(re.search(r"새로 색인한 문서|이미 모은 문서|배경|항목 문서 수집|이미 수집한 검색어", txt))
        shots["S13b"] = Shot("S13b", "S13b_진행상태_완료기록.png", ok and has_titles,
                             "완료 후 진행 기록(수집·색인 문서 제목)" if has_titles else "진행 기록에 수집 문서 줄이 없음")
        expand_last(page, "완료", want_open=False)      # 다시 접기
    expand_last(page, "근거 ")
    scroll_chat_bottom(page)
    page.screenshot(path=str(out / "S08_답변_근거목록.png"))
    txt = chat_text(page)
    nums_answer = set(re.findall(r"\[근거 (\d+)\]", txt.split("근거 ")[0])) if txt else set()
    shots["S08"] = Shot("S08", "S08_답변_근거목록.png", ok and "[근거" in txt and "항목 메타데이터" in txt,
                        "답변의 [근거 n]과 펼친 근거 목록(출처 종류 배지)" if ok else "답변 대기 시간 초과",
                        {"answer_numbers": sorted(nums_answer)})
    # 문서에 없는 질문
    ask(page, Q_UNKNOWN)
    ok9 = answered(page, 2)
    scroll_chat_bottom(page)
    page.screenshot(path=str(out / "S09_없는내용_질문.png"))
    txt = chat_text(page)
    last = txt.split(Q_UNKNOWN)[-1]
    abstain = "확인되지 않" in last
    notice = "인용된 근거가 없습니다" in last
    shots["S09"] = Shot("S09", "S09_없는내용_질문.png", ok9 and abstain,
                        f"'확인되지 않습니다' 답변 {'O' if abstain else 'X'} · 근거 없음 안내 {'O' if notice else '(인용 근거가 있어 안내 대신 근거 표시)'}")
    # 면책 안내
    dis = page.get_by_text("전문가의 진단", exact=False)
    if dis.count():
        dis.last.scroll_into_view_if_needed()
        page.screenshot(path=str(out / "S14_면책안내.png"))
        shots["S14"] = Shot("S14", "S14_면책안내.png", True, "답변 화면 아래 면책 안내")
    # 감염병별 대화 분리
    ask(page, Q_OTHER)
    ok10 = answered(page, 1)       # 수두 대화로 옮겨 가 첫 답
    scroll_chat_bottom(page)
    page.screenshot(path=str(out / "S10a_대화분리_수두.png"))
    txt = chat_text(page)
    sep = Q_OTHER in txt and Q_MAIN not in txt
    shots["S10a"] = Shot("S10a", "S10a_대화분리_수두.png", ok10 and sep,
                         "수두로 바꾸면 수두 대화만 보임" if sep else "다른 감염병 대화가 섞임")
    btn = page.locator('[class*="st-key-sum_go_"] button', has_text="홍역")
    if btn.count():
        btn.first.click()
        settle(page)
        scroll_chat_bottom(page)
        page.screenshot(path=str(out / "S10b_대화분리_홍역복귀.png"))
        txt = chat_text(page)
        back = Q_MAIN in txt and Q_OTHER not in txt
        shots["S10b"] = Shot("S10b", "S10b_대화분리_홍역복귀.png", back,
                             "홍역으로 돌아오면 홍역 대화만 그대로" if back else "대화가 섞이거나 사라짐")
    return list(shots.values())


def travel_flow(page, url: str, out: Path) -> list[Shot]:
    key = pick_country()
    s12, s19 = Shot("S12"), Shot("S19")
    if not key:
        s12.note = s19.note = "country_watch.json이 없거나 감염병이 연결된 나라가 없음"
        return [s12, s19]
    page.goto(f"{url}/?country={key}")
    settle(page, 2500)
    page.wait_for_timeout(2500)          # 팝업 애니메이션
    b = page.locator(f'[class*="st-key-travel_{key}"] button')
    if not b.count():
        s12.note = "여행 전 안내 버튼 없음"
        return [s12, s19]
    b.first.click()
    wait_text(page, "✈️ 여행 전 안내", 240)
    settle(page)
    expand_last(page, "근거 ")
    box = page.locator(f'[class*="st-key-travel_box_{key}"]')
    box.first.scroll_into_view_if_needed()
    page.screenshot(path=str(out / "S12_여행전안내_근거.png"))
    txt = box.first.inner_text() if box.count() else ""
    ok = "[근거" in txt and "공공데이터 API" in txt
    s12 = Shot("S12", "S12_여행전안내_근거.png", ok, f"나라 {key}: 안내 본문 [근거 n] + 근거 목록(공공데이터 API·메타데이터)")
    exp = box.locator('[data-testid="stExpander"]')
    if exp.count():
        exp.last.screenshot(path=str(out / "S19_근거_출처종류.png"))
        kinds = [k for k in ("항목 메타데이터", "위키백과", "공공데이터 API", "공공 통계") if k in exp.last.inner_text()]
        s19 = Shot("S19", "S19_근거_출처종류.png", len(kinds) >= 2, f"근거 목록의 출처 종류 배지: {', '.join(kinds)}")
    return [s12, s19]


def notice_flow(page, url: str, out: Path, sid: str, fname: str, expect: str, label: str) -> Shot:
    page.goto(url)
    settle(page, 2000)
    page.wait_for_timeout(1500)
    page.screenshot(path=str(out / fname))
    body = page.inner_text("body")
    leaks = leak_check(body)
    ok = expect in body and not leaks
    return Shot(sid, fname, ok, label + (f" · 노출: {leaks}" if leaks else ""))


def error_flow(page, url: str, out: Path) -> Shot:
    page.goto(url + "/chat")
    settle(page)
    ask(page, f"홍역 오류 화면 확인용 질문 {int(time.time())}")
    end = time.time() + 120
    while time.time() < end and not page.locator('[data-testid="stAlertContentError"]').count():
        page.wait_for_timeout(500)
    settle(page)
    scroll_chat_bottom(page)
    page.screenshot(path=str(out / "S21_오류화면_노출없음.png"))
    body = page.inner_text("body")
    leaks = leak_check(body)
    shown = "문제가 발생" in body or "사용 한도" in body
    return Shot("S21", "S21_오류화면_노출없음.png", shown and not leaks,
                ("오류 안내만 표시, 키·프롬프트·추적 정보 없음" if not leaks else f"노출: {leaks}") if shown
                else "오류 안내가 나오지 않음 (틀린 키로도 답이 생성됨: 저장 답변 등)")


def capture(out: Path, only: set[str] | None = None, offline: bool = False) -> list[Shot]:
    from playwright.sync_api import sync_playwright

    out.mkdir(parents=True, exist_ok=True)
    logs = out.parent / "logs"
    base_env = {"KDCA_SETTINGS": json.dumps({"answer_cache": False, "warmup": True}), "PYTHONIOENCODING": "utf-8"}
    want = (lambda *ids: not only or any(i in only for i in ids))
    shots: list[Shot] = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        ctx = browser.new_context(viewport=VIEW, locale="ko-KR", color_scheme="dark")
        if want("S08", "S09", "S10", "S13a", "S13b", "S14", "S12", "S19"):
            with App(PORT, base_env, logs / "app_main.log") as app:
                page = ctx.new_page()
                if want("S08", "S09", "S10", "S13a", "S13b", "S14"):
                    shots += chat_flow(page, app.url, out)
                if want("S12", "S19"):
                    shots += travel_flow(ctx.new_page(), app.url, out)
        if want("S13c"):
            empty = Path(tempfile.mkdtemp(prefix="kdca_empty_"))
            with App(PORT + 1, {**base_env, "KDCA_DATA_DIR": str(empty)}, logs / "app_noindex.log") as app:
                shots.append(notice_flow(ctx.new_page(), app.url, out, "S13c", "S13c_색인없음_안내.png",
                                         "색인이 없습니다", "색인 폴더가 빈 상태로 실행 → build_index 안내"))
        if want("S13d"):
            with App(PORT + 2, {**base_env, "OPENAI_API_KEY": ""}, logs / "app_nokey.log") as app:
                shots.append(notice_flow(ctx.new_page(), app.url, out, "S13d", "S13d_키없음_안내.png",
                                         "OPENAI_API_KEY", "OPENAI_API_KEY를 비우고 실행 → 변수 이름만 안내 (값 노출 없음)"))
        if want("S21"):
            bad = {"KDCA_OFFLINE_FAIL": "1"} if offline else {"OPENAI_API_KEY": "invalid-key-for-error-screen"}
            with App(PORT + 3, {**base_env, **bad}, logs / "app_error.log") as app:
                shots.append(error_flow(ctx.new_page(), app.url, out))
        browser.close()
    return shots


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="submission/evidence")
    ap.add_argument("--only", help="쉼표로 구분한 장면 ID (예: S08,S10)")
    args = ap.parse_args(argv)
    shots = capture(Path(args.out), set(args.only.split(",")) if args.only else None, paths.OFFLINE)
    for s in shots:
        print(f"[{'ok' if s.ok else '--'}] {s.id:<5} {s.file or '-':<28} {s.note}")
    (Path(args.out) / "screens.json").write_text(json.dumps([s.__dict__ for s in shots], ensure_ascii=False, indent=2),
                                                 encoding="utf-8")
    return 0 if all(s.ok for s in shots) else 1


if __name__ == "__main__":
    sys.exit(main())
