"""연령별·시기별 감염병 통계 (단계 2) - 감염병포털에서 내려받은 CSV·xlsx를 읽어 화면용 JSON으로 묶는다.

파일 종류는 파일 이름이 아니라 내용(상단의 '감염병', '조회기간', '조회구분' 칸)으로 판별한다.

| 종류 | 예 | 쓰임 |
| --- | --- | --- |
| basic_csv | 기본통계_성별_연령별.csv (전수감시 1~3급, 머리글 없음) | 연령대·성별 분포 |
| age_sex | 성매개감염병 선택됨- 임질.xlsx (표본감시, 연령별·성별, 주별) | 연령대·성별 분포 |
| sari | 중증급성호흡기감염증 선택됨 (n).xlsx (절기별, 7개 연령대, 주별 입원환자) | 연령대 분포 + 유행 시기 |
| flu_csv | 인플루엔자 선택됨.csv (절기별 주간 의사환자분율, 머리글 없음) | 유행 시기 |
| year_week | 수족구병·안과감염병 선택됨.xlsx (연도별 주간 의사환자분율) | 유행 시기 |
| pathogen_weekly | 급성호흡기·장관·엔테로바이러스 병원체 주별 검출 건수 | 유행 시기 |
"""
from __future__ import annotations

import csv
import json
import re
from datetime import datetime, timezone
from pathlib import Path

from data_prep.diseases import Disease
from data_prep.xlsx_lite import read_xlsx

AGE_GROUPS = ["0~4세", "5~9세", "10~14세", "15~19세", "20~24세", "25~29세", "30~34세", "35~39세",
              "40~44세", "45~49세", "50~54세", "55~59세", "60~64세", "65세 이상"]   # 포털 표기 그대로 14개
SEASON_WEEKS = [f"{w}주" for w in range(36, 54)] + [f"{w}주" for w in range(1, 36)]   # 36주 → 다음 해 35주 (53칸)
YEAR_WEEKS = [f"{w}주" for w in range(1, 54)]
PORTAL = "질병관리청 감염병포털"
PORTAL_URL = "https://dportal.kdca.go.kr/pot/is/summary.do"

# 병원체 이름 → 법정감염병 표시 이름 (철자가 다른 것만)
PATHOGEN_ALIASES = {"마이코플라즈마균": "마이코플라스마균 감염증", "인플루엔자 바이러스": "인플루엔자",
                    "인플루엔자": "인플루엔자", "코로나19 바이러스": "코로나바이러스감염증-19",
                    "코로나19": "코로나바이러스감염증-19"}
DATASET_UNITS = {
    "flu": "외래환자 1,000명당 인플루엔자 의사환자 수",
    "year_week": "외래환자 1,000명당 의사환자 수",
    "sari": "주간 입원환자 수 (중증급성호흡기감염증 표본감시)",
    "pathogen_weekly": "주간 병원체 검출 건수 (실험실 표본감시)",
}


# ---- 공통 ----------------------------------------------------------------------------
def clean(s: str) -> str:
    return " ".join(str(s).split())


def num(s: str) -> float | None:
    """'4,983' → 4983, '-'·'집계 중'·'' → None."""
    s = clean(s).replace(",", "")
    if not s or s in {"-", "집계 중", "집계중"}:
        return None
    try:
        v = float(s)
    except ValueError:
        return None
    return int(v) if v.is_integer() and "." not in s else v


def _int(s: str) -> int:
    v = num(s)
    return int(v) if v is not None else 0


def name_key(s: str) -> str:
    return re.sub(r"[\s·,]", "", s)


def read_meta(grid: list[list[str]]) -> dict[str, str]:
    """상단 표: [감염병 | 값 | 조회기간 | 값] / [조회구분 | 값 | 시도/시군구 | 값]."""
    meta = {}
    for row in grid[:6]:
        cells = [clean(c) for c in row]
        for i, c in enumerate(cells):
            if c in {"감염병", "조회기간", "조회구분", "시도/시군구"}:
                meta[c] = next((v for v in cells[i + 1:] if v), "")
    return meta


def subject_of(meta: dict) -> tuple[str, str]:
    """'성매개감염병 선택됨, 임질' → ('성매개감염병', '임질'). '(1기)매독' → '매독(1기)'."""
    parts = [p.strip() for p in meta.get("감염병", "").replace("선택됨", "").split(",")]
    group = parts[0].strip()
    name = parts[1] if len(parts) > 1 and parts[1] not in {"전체", "전체(전체)"} else group
    m = re.fullmatch(r"\(([^)]+)\)(.+)", name)
    if m:
        name = f"{m.group(2)}({m.group(1)})"
    return group, name


def _header_row(grid: list[list[str]]) -> int:
    for i, row in enumerate(grid):
        if len(row) > 1 and clean(row[1]).startswith("구분"):
            return i
    raise ValueError("'구분' 머리글 행을 찾지 못했습니다")


# ---- 파일 종류 판별 --------------------------------------------------------------------
def kind_of_xlsx(meta: dict) -> str:
    q, period = meta.get("조회구분", ""), meta.get("조회기간", "")
    if "연령별" in q and "성별" in q:
        return "age_sex"
    if q == "연령별" and "절기" in period:
        return "sari"
    if "주차별" in period:
        return "year_week"
    if "주별" in period and q in {"-", ""}:
        return "pathogen_weekly"
    return "unsupported"


_FLU_LABEL = re.compile(r"\((\d{4})-(\d{4})절기\)")


def kind_of_csv(rows: list[list[str]]) -> str:
    if rows and _FLU_LABEL.search(rows[0][0]):
        return "flu_csv"
    if rows and rows[0][0] in {"계", *AGE_GROUPS} and len(rows[0]) > 1 and rows[0][1] in {"계", "남", "여"}:
        return "basic_csv"
    return "unsupported"


def read_csv_rows(path: Path) -> list[list[str]]:
    raw = Path(path).read_bytes()
    for enc in ("utf-8-sig", "cp949"):
        try:
            text = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    else:
        raise ValueError(f"{path.name}: 인코딩을 알 수 없습니다")
    return [[clean(c) for c in r] for r in csv.reader(text.splitlines()) if any(c.strip() for c in r)]


# ---- 형식별 파서 -----------------------------------------------------------------------
def parse_basic_csv(rows: list[list[str]], columns: list[str]) -> dict[str, dict]:
    """머리글 없는 기본통계: [연령, 성별, 감염병1, 감염병2, ...]. columns = 감염병 이름 순서."""
    width = max(len(r) for r in rows) - 2
    while width and all(len(r) < width + 2 or r[width + 1] == "" for r in rows):
        width -= 1   # 줄 끝 쉼표로 생긴 빈 열
    if width != len(columns):
        raise ValueError(f"기본통계 열 수 {width}개, 열 이름표 {len(columns)}개가 다릅니다 "
                         "(config/age_stats_columns.json 확인)")
    out = {c: {"by_age": {a: [0, 0] for a in AGE_GROUPS}, "total": 0} for c in columns}
    for r in rows:
        age, sex, vals = r[0], r[1], r[2:2 + width]
        for c, v in zip(columns, vals):
            n = _int(v)
            if age == "계" and sex == "계":
                out[c]["total"] = n
            elif age in AGE_GROUPS and sex in {"남", "여"}:
                out[c]["by_age"][age][0 if sex == "남" else 1] = n
    return out


def parse_age_sex(grid: list[list[str]]) -> dict:
    """연령별·성별 주간표의 '계' 행 → 연령대별 [남, 여]."""
    h = _header_row(grid)
    ages, sexes = grid[h], grid[h + 1]
    total_row = next(r for r in grid[h + 2:] if clean(r[1]) == "계")
    by_age = {a: [0, 0] for a in AGE_GROUPS}
    total = 0
    for c in range(3, len(ages)):
        age, sex = clean(ages[c]), clean(sexes[c])
        if sex not in {"남", "여"}:
            continue
        n = _int(total_row[c])
        if age == "계":
            total += n
        elif age in by_age:
            by_age[age][0 if sex == "남" else 1] = n
    return {"by_age": by_age, "total": total}


def season_slot(year: int, week: int) -> tuple[str, int]:
    """(연도, 주) → ('2025-2026', 0~52). 36주부터 다음 해 35주까지가 한 절기."""
    if week >= 36:
        return f"{year}-{year + 1}", week - 36
    return f"{year - 1}-{year}", 17 + week


def parse_sari(grid: list[list[str]]) -> dict:
    """절기별 연령대 주간 입원환자 → {'ages': [...], 'seasons': {절기: {연령: [53칸]}}}."""
    h = _header_row(grid)
    ages = [clean(a) for a in grid[h][3:] if clean(a)]
    seasons: dict[str, dict[str, list]] = {}
    for r in grid[h + 1:]:
        y, w = clean(r[1]), clean(r[2])
        if not (y.isdigit() and w.isdigit()):
            continue
        vals = [num(v) for v in r[3:3 + len(ages)]]
        if all(v is None for v in vals):
            continue
        season, slot = season_slot(int(y), int(w))
        s = seasons.setdefault(season, {a: [None] * 53 for a in ages})
        for a, v in zip(ages, vals):
            s[a][slot] = v
    return {"ages": ages, "seasons": seasons}


def parse_year_week(grid: list[list[str]]) -> dict[str, list]:
    """연도 × 주차 표 → {'2021': [53칸]}."""
    h = _header_row(grid)
    weeks = [int(re.sub(r"\D", "", w)) for w in grid[h][2:] if re.sub(r"\D", "", clean(w))]
    out = {}
    for r in grid[h + 1:]:
        y = re.sub(r"\D", "", clean(r[1]))
        if not y:
            continue
        series = [None] * 53
        for w, v in zip(weeks, r[2:2 + len(weeks)]):
            if 1 <= w <= 53:
                series[w - 1] = num(v)
        if any(v is not None for v in series):
            out[y] = series
    return out


def parse_pathogen_weekly(grid: list[list[str]]) -> dict[str, dict[str, list]]:
    """병원체 주별 검출 건수 → {병원체: {'2021': [53칸]}}. 파일마다 묶음이 달라 겹치는 '계' 열은 뺀다."""
    h = _header_row(grid)
    groups, names = [clean(n) for n in grid[h]], [clean(n) for n in grid[h + 1]]
    if "신고수" in names:   # 기생충감염증: [감염병 | 신고수, 기관당 신고수] → 신고수 열만
        cols = [(c, groups[c]) for c in range(2, len(names)) if names[c] == "신고수"]
    else:
        cols = [(c, names[c]) for c in range(3, len(names)) if names[c] and names[c] != "계"]
    out: dict[str, dict[str, list]] = {n: {} for _, n in cols}
    for r in grid[h + 2:]:
        y, w = clean(r[1]).removesuffix("년"), re.sub(r"\D", "", clean(r[2]))
        if not (y.isdigit() and w.isdigit()) or not 1 <= int(w) <= 53:
            continue
        for c, n in cols:
            out[n].setdefault(y, [None] * 53)[int(w) - 1] = num(r[c]) if c < len(r) else None
    return out


def parse_flu_csv(rows: list[list[str]]) -> dict[str, list]:
    """'전체(2024-2025절기)', 53칸(36주~35주) → {'2024-2025': [...]}"""
    out = {}
    for r in rows:
        m = _FLU_LABEL.search(r[0])
        if not m:
            continue
        vals = [num(v) for v in r[1:54]]
        out[f"{m.group(1)}-{m.group(2)}"] = vals + [None] * (53 - len(vals))
    return out


# ---- 법정감염병 목록과 연결 -------------------------------------------------------------
class DiseaseIndex:
    def __init__(self, diseases: list[Disease]):
        self.diseases = diseases
        self.by_name = {name_key(d.name): d for d in diseases}
        self.by_display: dict[str, list[Disease]] = {}
        for d in diseases:
            self.by_display.setdefault(name_key(d.display_name), []).append(d)

    def find(self, name: str, group: str = "") -> Disease | None:
        name = PATHOGEN_ALIASES.get(name, name)
        if name_key(name) in self.by_name:
            return self.by_name[name_key(name)]
        for cand in (name, f"{name} 감염증", f"{name}감염증"):
            found = self.by_display.get(name_key(cand), [])
            if found:
                same = [d for d in found if group and name_key(d.group) == name_key(group)]
                return (same or found)[0]
        return None


def _link(index: DiseaseIndex, name: str, group: str = "") -> dict:
    d = index.find(name, group)
    return {"disease_id": d.disease_id, "grade": d.grade} if d else {"disease_id": "", "grade": ""}


# ---- 전체 묶기 -------------------------------------------------------------------------
def build_age_stats(files: list[Path], diseases: list[Disease], columns_cfg: dict) -> tuple[dict, list[dict]]:
    """(age_stats dict, 파일별 처리 기록)."""
    index = DiseaseIndex(diseases)
    notifiable: dict[str, dict] = {}
    sti: dict[str, dict] = {}
    sti_period = ""
    sari: dict = {"ages": [], "seasons": {}}
    trends: dict[str, dict] = {}
    log: list[dict] = []

    def trend(tid: str, label: str, unit: str, kind: str) -> dict:
        return trends.setdefault(tid, {"id": tid, "label": label, "unit": unit, "kind": kind,
                                       "x": SEASON_WEEKS if kind == "season" else YEAR_WEEKS, "items": {}})

    for f in sorted(files):
        rec = {"file": f.name, "kind": "unsupported", "used": False, "note": ""}
        log.append(rec)
        try:
            if f.suffix.lower() == ".csv":
                rows = read_csv_rows(f)
                rec["kind"] = kind_of_csv(rows)
                if rec["kind"] == "basic_csv":
                    cols = columns_cfg.get("columns", [])
                    for name, v in parse_basic_csv(rows, cols).items():
                        if v["total"]:
                            notifiable[name] = {**v, **_link(index, name)}
                    rec["note"] = f"감염병 {len(cols)}열 (값 있는 {len(notifiable)}종)"
                elif rec["kind"] == "flu_csv":
                    seasons = parse_flu_csv(rows)
                    t = trend("flu", "인플루엔자 의사환자분율", DATASET_UNITS["flu"], "season")
                    t["items"]["인플루엔자"] = {**_link(index, "인플루엔자"), "series": seasons}
                    rec["note"] = f"절기 {len(seasons)}개"
            elif f.suffix.lower() == ".xlsx":
                grid = read_xlsx(f)
                meta = read_meta(grid)
                rec["kind"] = kind_of_xlsx(meta)
                group, name = subject_of(meta)
                if rec["kind"] == "age_sex":
                    sti[name] = {**parse_age_sex(grid), **_link(index, name)}
                    sti_period = sti_period or meta.get("조회기간", "")
                    rec["note"] = f"{name} · {meta.get('조회기간', '')}"
                elif rec["kind"] == "sari":
                    p = parse_sari(grid)
                    sari["ages"] = sari["ages"] or p["ages"]
                    for s, v in p["seasons"].items():   # 같은 절기가 여러 파일에 있으면 빈 칸만 채운다
                        cur = sari["seasons"].setdefault(s, {a: [None] * 53 for a in p["ages"]})
                        for a, vals in v.items():
                            cur[a] = [x if x is not None else y for x, y in zip(cur[a], vals)]
                    rec["note"] = "절기 " + ", ".join(sorted(p["seasons"]))
                elif rec["kind"] == "year_week":
                    t = trend(f"yw_{name}", f"{name} 의사환자분율", DATASET_UNITS["year_week"], "year")
                    target = name if index.find(name) else ""
                    t["items"][name] = {**_link(index, target or name), "series": parse_year_week(grid)}
                    rec["note"] = f"{name} · {meta.get('조회기간', '')}"
                elif rec["kind"] == "pathogen_weekly":
                    data = parse_pathogen_weekly(grid)
                    tid = f"pw_{group}"
                    t = trend(tid, f"{group} 병원체 검출", DATASET_UNITS["pathogen_weekly"], "year")
                    for pname, series in data.items():
                        key = pname if pname not in t["items"] else f"{pname} ({name})"
                        link = _link(index, pname, group)
                        if not link["disease_id"] and group == "엔테로바이러스감염증":
                            link = _link(index, "엔테로바이러스 감염증")
                        t["items"][key] = {**link, "series": series}
                    rec["note"] = f"{group} · 병원체 {len(data)}개"
            rec["used"] = rec["kind"] != "unsupported"
            if not rec["used"]:
                rec["note"] = rec["note"] or "연령·시기 화면에 쓰지 않는 형식 (월별·연도별·신고수 등)"
        except Exception as exc:  # noqa: BLE001 - 파일 하나가 이상해도 나머지는 처리
            rec.update(kind="error", used=False, note=str(exc))

    if sari["seasons"]:
        t = trend("sari", "중증급성호흡기감염증 입원환자", DATASET_UNITS["sari"], "season")
        for age in sari["ages"]:
            t["items"][age] = {"disease_id": "", "grade": "",
                               "series": {s: v[age] for s, v in sorted(sari["seasons"].items())}}
        totals = {}
        for s, v in sari["seasons"].items():
            totals[s] = [sum(x for x in col if x is not None) if any(x is not None for x in col) else None
                         for col in zip(*v.values())]
        t["items"] = {"전체": {"disease_id": "", "grade": "", "series": dict(sorted(totals.items()))}, **t["items"]}

    sari_by_age = {}
    for s, v in sari["seasons"].items():
        sari_by_age[s] = {a: sum(x for x in col if x is not None) for a, col in v.items()}

    data = {
        "generated_at": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
        "source": PORTAL, "source_url": PORTAL_URL,
        "age_groups": AGE_GROUPS,
        "age_sets": {
            "notifiable": {"label": "전수감시 (1~3급)", "period": columns_cfg.get("period", ""),
                           "columns_verified": bool(columns_cfg.get("verified")),
                           "note": "기본통계 성별·연령별 신고 건수",
                           "diseases": dict(sorted(notifiable.items(), key=lambda kv: -kv[1]["total"]))},
            "sti": {"label": "성매개감염병 표본감시 (4급 등)", "period": sti_period, "columns_verified": True,
                    "note": "표본감시 기관 신고 건수 (전체 환자 수가 아님)",
                    "diseases": dict(sorted(sti.items(), key=lambda kv: -kv[1]["total"]))},
        },
        "sari_by_age": {"ages": sari["ages"], "seasons": dict(sorted(sari_by_age.items()))},
        "trends": [trends[k] for k in sorted(trends, key=_trend_order)],
    }
    return data, log


def _trend_order(tid: str) -> tuple:
    order = ["flu", "sari", "pw_급성호흡기감염증", "yw_수족구병", "pw_엔테로바이러스감염증", "pw_장관감염증"]
    return (order.index(tid) if tid in order else len(order), tid)


def load_columns(path: Path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def save(path: Path, data: dict) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
