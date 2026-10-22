"""연령별 알아보기: data/processed/age_stats.json 읽기와 화면용 계산 (Streamlit 없이 테스트 가능)."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

KIDS = ("0~4세", "5~9세")
OLD = ("65세 이상",)


@dataclass
class AgeDisease:
    name: str
    set_id: str
    disease_id: str
    grade: str
    by_age: dict[str, list[int]]
    total: int

    def count(self, age: str, sex: str = "전체") -> int:
        m, f = self.by_age.get(age, [0, 0])
        return m + f if sex == "전체" else (m if sex == "남" else f)

    def sex_total(self, sex: str) -> int:
        return sum(self.count(a, sex) for a in self.by_age)


@dataclass
class AgeSet:
    id: str
    label: str
    period: str
    verified: bool
    note: str
    diseases: dict[str, AgeDisease] = field(default_factory=dict)


@dataclass
class TrendItem:
    name: str
    disease_id: str
    series: dict[str, list[float | None]]


@dataclass
class Trend:
    id: str
    label: str
    unit: str
    kind: str              # "season"(36주~35주) | "year"(1~53주)
    x: list[str]
    items: dict[str, TrendItem]


@dataclass
class AgeStats:
    loaded: bool = False
    generated_at: str = ""
    source: str = ""
    source_url: str = ""
    age_groups: list[str] = field(default_factory=list)
    sets: dict[str, AgeSet] = field(default_factory=dict)
    sari_ages: list[str] = field(default_factory=list)
    sari_seasons: dict[str, dict[str, int]] = field(default_factory=dict)
    trends: dict[str, Trend] = field(default_factory=dict)


def load_age_stats(path: Path) -> AgeStats:
    p = Path(path)
    if not p.exists():
        return AgeStats()
    d = json.loads(p.read_text(encoding="utf-8"))
    sets = {}
    for sid, s in d.get("age_sets", {}).items():
        sets[sid] = AgeSet(sid, s["label"], s.get("period", ""), bool(s.get("columns_verified")), s.get("note", ""),
                           {n: AgeDisease(n, sid, v.get("disease_id", ""), str(v.get("grade", "")),
                                          {a: list(x) for a, x in v["by_age"].items()}, int(v["total"]))
                            for n, v in s.get("diseases", {}).items()})
    trends = {t["id"]: Trend(t["id"], t["label"], t["unit"], t["kind"], list(t["x"]),
                             {n: TrendItem(n, v.get("disease_id", ""), v["series"]) for n, v in t["items"].items()})
              for t in d.get("trends", [])}
    sari = d.get("sari_by_age", {})
    return AgeStats(True, d.get("generated_at", ""), d.get("source", ""), d.get("source_url", ""),
                    list(d.get("age_groups", [])), sets, list(sari.get("ages", [])), sari.get("seasons", {}), trends)


# ---- 연령대 → 감염병 ------------------------------------------------------------------
def top_for_age(aset: AgeSet, age: str, sex: str = "전체", by: str = "count", n: int = 10,
                min_total: int = 30) -> list[dict]:
    """이 연령대 신고 건수(by='count') 또는 이 연령대 비중(by='share')이 큰 감염병.

    비중 = 이 연령대 건수 ÷ 그 감염병 전체 건수. 건수가 적으면 비중이 들쭉날쭉해서 min_total 이상만 비중순에 넣는다.
    """
    rows = []
    for d in aset.diseases.values():
        c = d.count(age, sex)
        tot = d.sex_total(sex)
        if c <= 0 or tot <= 0:
            continue
        if by == "share" and tot < min_total:
            continue
        rows.append({"name": d.name, "count": c, "share": c / tot, "disease": d})
    key = (lambda r: (-r["share"], -r["count"])) if by == "share" else (lambda r: (-r["count"], -r["share"]))
    return sorted(rows, key=key)[:n]


# ---- 감염병 → 연령대 ------------------------------------------------------------------
def profile(d: AgeDisease, ages: list[str]) -> dict:
    counts = {a: d.count(a) for a in ages}
    known = sum(counts.values())
    peak = max(ages, key=lambda a: counts[a]) if known else ""
    male, female = d.sex_total("남"), d.sex_total("여")
    return {
        "total": d.total or known, "known": known, "peak_age": peak, "peak_count": counts.get(peak, 0),
        "peak_share": counts[peak] / known if known else 0.0,
        "male": male, "female": female, "male_share": male / (male + female) if male + female else 0.0,
        "kids_share": sum(counts.get(a, 0) for a in KIDS) / known if known else 0.0,
        "old_share": sum(counts.get(a, 0) for a in OLD) / known if known else 0.0,
    }


# ---- 유행 시기 ------------------------------------------------------------------------
def week_number(label: str) -> int:
    return int("".join(ch for ch in label if ch.isdigit()))


def week_month(week: int, year: int) -> int:
    """ISO 주 → 그 주 월요일의 달 (53주가 없는 해는 52주로)."""
    try:
        return date.fromisocalendar(year, week, 1).month
    except ValueError:
        return date.fromisocalendar(year, 52, 1).month


def _slot_year(trend: Trend, key: str, slot: int) -> int:
    if trend.kind == "season":
        start = int(key.split("-")[0])
        return start if slot < 18 else start + 1
    return int(key)


def trend_summary(trend: Trend, item: TrendItem) -> dict:
    """최근 값, 해마다(절기마다) 정점, 보통 유행하는 달."""
    keys = sorted(item.series)
    latest = None
    for k in reversed(keys):
        vals = item.series[k]
        idx = max((i for i, v in enumerate(vals) if v is not None), default=None)
        if idx is not None:
            latest = {"key": k, "week": trend.x[idx], "value": vals[idx],
                      "year": _slot_year(trend, k, idx), "complete": idx >= 51}
            break
    peaks = []
    for k in keys:
        vals = item.series[k]
        pairs = [(v, i) for i, v in enumerate(vals) if v is not None]
        if not pairs:
            continue
        v, i = max(pairs)
        if v <= 0:
            continue
        filled = len(pairs) >= 45   # 거의 다 찬 해·절기만 '보통 유행 시기' 계산에 사용
        peaks.append({"key": k, "week": trend.x[i], "value": v, "slot": i, "complete": filled,
                      "month": week_month(week_number(trend.x[i]), _slot_year(trend, k, i))})
    full = [p for p in peaks if p["complete"]]
    typical = ""
    if full:   # 가운데 값 (절기는 7월부터 순서를 세어 12월·1월이 이어지게)
        order = sorted((p["month"] for p in full), key=lambda m: (m - 7) % 12 if trend.kind == "season" else m)
        typical = f"{order[(len(order) - 1) // 2]}월"
    return {"latest": latest, "peaks": peaks, "typical_month": typical}


def fmt_value(v: float | None) -> str:
    if v is None:
        return "-"
    return f"{v:,.1f}" if isinstance(v, float) and not float(v).is_integer() else f"{int(v):,}"
