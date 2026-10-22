"""연령별 통계 테스트용 합성 파일 (감염병포털 내려받기 형식을 흉내 냄. 값은 가짜)."""
from __future__ import annotations

import json
from pathlib import Path

from data_prep import settings as paths
from data_prep.age_stats import AGE_GROUPS
from data_prep.xlsx_lite import write_xlsx


def _meta(subject: str, period: str, query: str) -> list[list[str]]:
    return [[], ["", "감염병", "", subject, "", "", "", "조회기간", "", period],
            [], ["", "조회구분", "", query, "", "", "", "시도/시군구", "", "-"], [], []]


def basic_csv_rows() -> list[list[str]]:
    """68열 기본통계: 수두(17번째 열) = 어린이, CRE(36번째 열) = 65세 이상, 나머지 0."""
    cols = json.loads((paths.CONFIG / "age_stats_columns.json").read_text(encoding="utf-8"))["columns"]
    i_chick, i_cre = cols.index("수두"), cols.index("카바페넴내성장내세균목(CRE) 감염증")
    chick = {"0~4세": (80, 60), "5~9세": (70, 70), "10~14세": (12, 8)}     # (남, 여)
    cre = {"65세 이상": (90, 110)}

    def cell(age: str, sex: str, table: dict) -> int:
        pairs = list(table.values()) if age == "계" else [table.get(age, (0, 0))]
        return sum(m if sex == "남" else f if sex == "여" else m + f for m, f in pairs)
    rows = []
    for age in ["계", *AGE_GROUPS]:
        for sex in ["계", "남", "여"]:
            v = [0] * len(cols)
            v[i_chick], v[i_cre] = cell(age, sex, chick), cell(age, sex, cre)
            rows.append([age, sex, *map(str, v), ""])
    return rows


def make_stats_dir(d: Path) -> Path:
    d.mkdir(parents=True, exist_ok=True)
    (d / "기본통계_성별_연령별.csv").write_bytes(
        "\n".join(",".join(r) for r in basic_csv_rows()).encode("cp949"))
    flu = [f"전체(2024-2025절기)," + ",".join(["2.0"] * 17 + [""] + ["99.8"] + ["5.0"] * 34) + ",",
           f"전체(2025-2026절기)," + ",".join(["3.0"] * 12 + ["40.0"] + ["4.0"] * 4 + [""] + ["6.0"] * 35) + ","]
    (d / "인플루엔자 선택됨.csv").write_bytes("\n".join(flu).encode("cp949"))

    ages = ["계", *AGE_GROUPS]
    sti = _meta("성매개감염병 선택됨, 임질", "2021년 01주 ~ 2026년 43주, 주별", "연령별, 성별")
    sti.append(["", "구분\n(임질)", "구분\n(임질)", *[a for a in ages for _ in (0, 1)]])
    sti.append(["", "구분\n(임질)", "구분\n(임질)", *["남", "여"] * len(ages)])
    tot = []
    for a in ages:
        tot += {"계": ["1,000", "400"], "20~24세": ["600", "300"], "25~29세": ["400", "100"]}.get(a, ["0", "0"])
    sti.append(["", "계", "계", *tot])
    sti.append(["", "2021년", "01주", *["1"] * len(tot)])
    write_xlsx(d / "성매개감염병 선택됨- 임질.xlsx", sti)

    sari_ages = ["0세", "1-6세", "7-12세", "13-18세", "19-49세", "50-64세", "65세 이상"]
    for n, (season, y) in enumerate([("2024 ~ 2025", 2024), ("2025 ~ 2026", 2025)]):
        g = _meta("중증급성호흡기감염증 선택됨", f"\n\t\t{season}\n   , 절기", "연령별")
        g.append(["", "구분", "구분", *sari_ages])
        for w in [36, 52]:
            g.append(["", str(y), str(w), "1", "2", "0", "0", "1", "2", str(10 + w - 36)])
        g.append(["", str(y + 1), "1", "2", "3", "0", "0", "1", "2", "30"])
        g.append(["", str(y + 1), "2", "-", "-", "-", "-", "-", "-", "-"])
        write_xlsx(d / f"중증급성호흡기감염증 선택됨 ({n}).xlsx", g)

    yw = _meta("수족구병 선택됨", "2021년 ~ 2026년, 주차별", "연령(전체)")
    yw.append(["", "구분", *[f"{w:02d}주" for w in range(1, 54)]])
    for y in (2024, 2025):
        yw.append(["", f"{y}년", *[str(50.0 if w == 30 else 1.0) for w in range(1, 54)]])
    write_xlsx(d / "수족구병 선택됨.xlsx", yw)

    pw = _meta("급성호흡기감염증 선택됨, 바이러스(전체)", "2021년 01주 ~ 2026년 43주, 주별", "-")
    pw.append(["", "구분", "구분", "계", "바이러스", "바이러스"])
    pw.append(["", "구분", "구분", "계", "아데노바이러스", "호흡기세포융합바이러스"])
    for w in range(1, 54):
        pw.append(["", "2025", f"{w:02d}", str(w + 1), "1", str(w)])
    pw.append(["", "2026", "01", "집계 중", "집계 중", "집계 중"])
    write_xlsx(d / "급성호흡기감염증 선택됨- 바이러스[전체].xlsx", pw)

    other = _meta("의료관련감염병 선택됨", "2026년, 월별", "혈액검체발생률")
    other.append(["", "구분", "MRSA 감염증"])
    other.append(["", "01월", "0.09"])
    write_xlsx(d / "의료관련감염병 선택됨.xlsx", other)
    return d


def build_sample(out: Path, stats_dir: Path) -> Path:
    from data_prep.age_stats import build_age_stats, load_columns, save
    from data_prep.diseases import load_diseases
    data, _ = build_age_stats(sorted(stats_dir.glob("*")), load_diseases(paths.DISEASES_CSV),
                              load_columns(paths.CONFIG / "age_stats_columns.json"))
    save(out, data)
    return out
