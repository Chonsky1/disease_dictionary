"""API 키 노출 검사 (NF2003, 체크리스트 21). 키 값은 절대 출력하지 않는다.

python -m scripts.scan_secrets                  # git에 올라가는 파일 전체
python -m scripts.scan_secrets submission       # 제출 폴더도 함께

1) .env가 .gitignore에 있고 git에 올라가지 않았는지
2) .env에 적힌 실제 키 값(이름에 KEY·TOKEN·SECRET이 들어간 변수)이 다른 파일에 들어 있는지
3) 흔한 키 모양 (sk-…, 공공데이터 serviceKey=…, Bearer …)
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

from data_prep import settings as paths

PATTERNS = {
    "OpenAI 형식 키": re.compile(r"sk-[A-Za-z0-9_\-]{20,}"),
    "serviceKey 값": re.compile(r"serviceKey=[A-Za-z0-9%+/=]{20,}"),
    "Bearer 토큰": re.compile(r"Bearer\s+[A-Za-z0-9._\-]{20,}"),
}
TEXT_EXT = {".py", ".md", ".json", ".txt", ".toml", ".csv", ".yaml", ".yml", ".html", ".js", ".css", ".example", ""}


def env_secrets() -> dict[str, str]:
    p = paths.ROOT / ".env"
    out = {}
    if p.exists():
        for line in p.read_text(encoding="utf-8").splitlines():
            m = re.match(r"\s*([A-Z0-9_]+)\s*=\s*(.+)", line)
            if m and re.search(r"KEY|TOKEN|SECRET", m.group(1)):
                v = m.group(2).strip().strip('"').strip("'")
                if len(v) >= 12:
                    out[m.group(1)] = v
    return out


def tracked_files() -> list[Path]:
    try:
        names = subprocess.run(["git", "ls-files"], cwd=paths.ROOT, capture_output=True, text=True, check=True).stdout
        return [paths.ROOT / n for n in names.splitlines()]
    except Exception:  # noqa: BLE001 - git이 없으면 폴더 전체
        return [p for p in paths.ROOT.rglob("*") if p.is_file() and ".git" not in p.parts and "data" not in p.parts]


def main(argv=None) -> int:
    extra = [Path(a) for a in (argv if argv is not None else sys.argv[1:])]
    files = tracked_files() + [p for d in extra if d.exists() for p in d.rglob("*") if p.is_file()]
    problems = []
    gi = (paths.ROOT / ".gitignore").read_text(encoding="utf-8") if (paths.ROOT / ".gitignore").exists() else ""
    env_ignored = any(l.strip() in (".env", "/.env", ".env*") for l in gi.splitlines())
    env_tracked = any(f.name == ".env" for f in files)
    print(f"[1] .gitignore에 .env: {'있음' if env_ignored else '없음'} · git에 .env 포함: {'예' if env_tracked else '아니오'}")
    if not env_ignored or env_tracked:
        problems.append(".env 관리")
    secrets = env_secrets()
    print(f"[2] .env의 키 변수 {len(secrets)}개 ({', '.join(secrets) or '없음'}) 값을 {len(files)}개 파일에서 검색")
    scanned = 0
    for f in files:
        if f.suffix.lower() not in TEXT_EXT or not f.exists() or f.stat().st_size > 3_000_000 or f.name == ".env":
            continue
        try:
            text = f.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        scanned += 1
        rel = f.relative_to(paths.ROOT) if f.is_relative_to(paths.ROOT) else f
        for name, value in secrets.items():
            if value in text:
                problems.append(f"{rel}: {name} 값")
        for label, pat in PATTERNS.items():
            for m in pat.finditer(text):
                line = text.count("\n", 0, m.start()) + 1
                problems.append(f"{rel}:{line}: {label} ({m.group(0)[:6]}…)")
    print(f"[3] 키 모양 검사: 텍스트 파일 {scanned}개")
    if problems:
        print("[결과] 발견:", *problems, sep="\n  - ")
        return 1
    print("[결과] 키 값·키 모양 문자열 없음 (값은 출력하지 않음)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
