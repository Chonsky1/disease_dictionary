"""data/raw/csv/*.csv 를 모두 프로파일링해 EDA 보고서를 만든다.

열 구조가 데이터셋마다 다르고 미리 알 수 없으므로, 열의 역할(감염병명·연령·연도)을
값으로 추정하고 그 결과를 사람이 확인하도록 보고서에 남긴다.

실행: python -m scripts.eda_report [--csv-dir 경로]
결과: reports/eda_report.md, data/processed/name_match.csv, data/processed/age_label_map.csv
"""
from __future__ import annotations

import argparse
import csv
import re
import sys
from pathlib import Path

from data_prep import settings
from data_prep.age_groups import AgeBand, is_age_label, load_common_groups, map_to_common, parse_age_label
from data_prep.csv_loader import NOT_APPLICABLE, load_dataframe, to_number
from data_prep.names import load_aliases, match_names, name_key

YEAR_RE = re.compile(r"(19|20)\d{2}")


def load_seed_names() -> list[str]:
    with open(settings.SEED / "diseases_seed.csv", encoding="utf-8-sig", newline="") as f:
        return [r["name_ko"] for r in csv.DictReader(f)]


def column_profile(df) -> list[dict]:
    out = []
    for col in df.columns:
        s = df[col].astype(str).str.strip()
        nonempty = s[s != ""]
        numeric = nonempty.map(lambda v: isinstance(to_number(v), float)).mean() if len(nonempty) else 0
        out.append({"column": col, "filled": len(nonempty), "unique": nonempty.nunique(),
                    "dash": int(s.isin(NOT_APPLICABLE).sum()), "zero": int((s == "0").sum()),
                    "numeric_ratio": round(float(numeric), 2),
                    "sample": ", ".join(nonempty.drop_duplicates().head(3))})
    return out


def guess_role_columns(df, seed_keys: set[str]) -> dict:
    """값을 보고 감염병명 열, 연령 열, 연도 열을 추정한다."""
    best_name, best_name_ratio = None, 0.0
    best_age, best_age_ratio = None, 0.0
    for col in df.columns:
        vals = df[col].astype(str).str.strip()
        vals = vals[vals != ""].drop_duplicates()
        if vals.empty:
            continue
        name_ratio = vals.map(lambda v: name_key(v) in seed_keys).mean()
        age_ratio = vals.map(is_age_label).mean()
        if any(w in str(col) for w in ("연령", "나이", "age")):
            age_ratio += 0.01  # 동률이면 열 이름이 연령인 쪽을 고른다
        if name_ratio > best_name_ratio:
            best_name, best_name_ratio = col, name_ratio
        if age_ratio > best_age_ratio:
            best_age, best_age_ratio = col, min(age_ratio, 1.0)
    year_cols = [c for c in df.columns if YEAR_RE.fullmatch(str(c).replace("년", "").strip())]
    year_value_col = next((c for c in df.columns if any(w in str(c) for w in ("연도", "년도", "year"))), None)
    header_ages = [c for c in df.columns if is_age_label(c)]
    return {
        "disease_col": best_name if best_name_ratio >= 0.2 else None, "disease_ratio": round(best_name_ratio, 2),
        "age_col": best_age if best_age_ratio >= 0.5 else None, "age_ratio": round(best_age_ratio, 2),
        "age_in_header": header_ages, "year_cols": year_cols, "year_value_col": year_value_col,
    }


def md_table(rows: list[dict], cols: list[str]) -> list[str]:
    lines = ["| " + " | ".join(cols) + " |", "| " + " | ".join("---" for _ in cols) + " |"]
    for r in rows:
        lines.append("| " + " | ".join(str(r.get(c, "")).replace("|", "/") for c in cols) + " |")
    return lines


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv-dir", type=Path, default=settings.RAW_CSV)
    args = ap.parse_args(argv)
    settings.ensure_dirs()

    files = sorted(args.csv_dir.glob("*.csv"))
    if not files:
        print(f"[eda] {args.csv_dir} 에 CSV가 없습니다. 포털에서 내려받아 넣어 주세요.")
        return 1

    seed_names = load_seed_names()
    seed_keys = {name_key(n) for n in seed_names}
    aliases = load_aliases(settings.CONFIG / "name_aliases.json")
    groups = load_common_groups(settings.CONFIG / "age_groups.json")

    report = ["# EDA 보고서", "", f"- 대상 파일: {len(files)}개, 시드 항목: {len(seed_names)}건", ""]
    name_rows, age_rows = [], []

    for path in files:
        df, meta = load_dataframe(path)
        roles = guess_role_columns(df, seed_keys)
        report += [f"## {path.name}", "",
                   f"- 인코딩 {meta['encoding']}, 헤더 줄 {meta['header_row'] + 1}행, 크기 {len(df)}행 × {len(df.columns)}열",
                   f"- 표 위 설명 줄: {' / '.join(p for p in meta['preamble'] if p) or '없음'}",
                   f"- 추정 감염병명 열: {roles['disease_col']} (시드 일치 {roles['disease_ratio']:.0%})",
                   f"- 추정 연령 열: {roles['age_col']} (해석 성공 {roles['age_ratio']:.0%}), 헤더에 연령 구간: {len(roles['age_in_header'])}개",
                   f"- 연도 열(넓은 형식): {roles['year_cols'] or '없음'}, 연도 값 열(긴 형식): {roles['year_value_col']}",
                   "", *md_table(column_profile(df), ["column", "filled", "unique", "dash", "zero", "numeric_ratio", "sample"]), ""]

        if roles["disease_col"]:
            matched, unmatched = match_names(df[roles["disease_col"]].tolist(), seed_names, aliases)
            total = len(matched) + len(unmatched)
            report += [f"- 이름 조인: {len(matched)}/{total} 매칭, 미매칭 {len(unmatched)}건 (아래 표 → config/name_aliases.json)", ""]
            if unmatched:
                report += md_table([{"name": u["name"], "candidates": ", ".join(u["candidates"])} for u in unmatched],
                                   ["name", "candidates"]) + [""]
            name_rows += [{"file": path.name, "source_name": k, "seed_name": v} for k, v in matched.items()]
            name_rows += [{"file": path.name, "source_name": u["name"], "seed_name": ""} for u in unmatched]

        labels = (df[roles["age_col"]].drop_duplicates().tolist() if roles["age_col"] else []) + roles["age_in_header"]
        for label in labels:
            band = parse_age_label(label)
            if isinstance(band, AgeBand):
                gid, ratio, exact = map_to_common(band, groups)
                age_rows.append({"file": path.name, "label": label, "lo": band.lo, "hi": band.hi,
                                 "common_group": gid, "overlap": ratio, "exact": exact})
            else:
                age_rows.append({"file": path.name, "label": label, "lo": "", "hi": "",
                                 "common_group": band or "해석 불가", "overlap": "", "exact": ""})
        partial = [r for r in age_rows if r["file"] == path.name and r["exact"] is False]
        if labels:
            report += [f"- 연령 라벨 {len(labels)}개, 공통 연령대에 걸치는 구간 {len(partial)}개: "
                       f"{', '.join(str(r['label']) for r in partial) or '없음'}", ""]

    for name, rows, cols in (("name_match.csv", name_rows, ["file", "source_name", "seed_name"]),
                             ("age_label_map.csv", age_rows,
                              ["file", "label", "lo", "hi", "common_group", "overlap", "exact"])):
        with open(settings.PROCESSED / name, "w", encoding="utf-8-sig", newline="") as f:
            w = csv.DictWriter(f, fieldnames=cols)
            w.writeheader()
            w.writerows(rows)

    out = settings.REPORTS / "eda_report.md"
    out.write_text("\n".join(report) + "\n", encoding="utf-8")
    print(f"[eda] {len(files)}개 파일 → {out.relative_to(settings.ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
