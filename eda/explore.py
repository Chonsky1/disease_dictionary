# %% [markdown]
# # 감염병 데이터 EDA (VS Code에서 셀 단위 실행)
# scripts/eda_report.py가 만든 보고서로 열 역할을 확인한 뒤, 이 파일에서 데이터셋별 질문에 답한다.
# 아래 COLUMN 설정은 보고서를 보고 실제 열 이름으로 바꾼다.

# %%
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] if "__file__" in globals() else Path.cwd()
sys.path.insert(0, str(ROOT))

import matplotlib.pyplot as plt
import pandas as pd

from data_prep import settings
from data_prep.age_groups import AgeBand, load_common_groups, map_to_common, parse_age_label
from data_prep.csv_loader import load_dataframe, to_number

plt.rcParams["font.family"] = ["AppleGothic", "Malgun Gothic", "NanumGothic", "DejaVu Sans"]
plt.rcParams["axes.unicode_minus"] = False
FIGS = settings.REPORTS / "figs"
FIGS.mkdir(parents=True, exist_ok=True)

# %% 1. 파일 선택과 설정 ── 보고서를 보고 채운다
FILE = settings.RAW_CSV / "age_sex_incidence.csv"   # 예: 성별 연령별 발생현황
COLUMN = {
    "disease": "감염병명",      # 감염병명 열
    "year": "연도",            # 긴 형식일 때 연도 열, 넓은 형식이면 None
    "age": "연령",             # 연령이 행에 있으면 열 이름, 헤더에 있으면 None
    "sex": "성별",             # 없으면 None
}
df, meta = load_dataframe(FILE)
print(meta["encoding"], df.shape)
df.head(10)

# %% 2. 넓은 형식 → 긴 형식 (연령이 헤더에 있는 표)
def to_long(frame: pd.DataFrame, id_cols: list[str], var_name: str) -> pd.DataFrame:
    value_cols = [c for c in frame.columns if c not in id_cols]
    long = frame.melt(id_vars=id_cols, value_vars=value_cols, var_name=var_name, value_name="raw")
    long["value"] = long["raw"].map(to_number)
    long["not_applicable"] = long["raw"].astype(str).str.strip().isin({"-", "－"})
    return long

id_cols = [c for c in (COLUMN["disease"], COLUMN["year"], COLUMN["sex"]) if c and c in df.columns]
if COLUMN["age"] and COLUMN["age"] in df.columns:
    long = df.copy()
    value_col = [c for c in df.columns if c not in id_cols + [COLUMN["age"]]][-1]
    long["value"] = long[value_col].map(to_number)
    long = long.rename(columns={COLUMN["age"]: "age_label"})
else:
    long = to_long(df, id_cols, "age_label")
long.head()

# %% 3. 연령 라벨 → 공통 연령대
groups = load_common_groups(settings.CONFIG / "age_groups.json")

def to_group(label):
    band = parse_age_label(label)
    if not isinstance(band, AgeBand):
        return pd.Series({"age_group": band, "exact": None})
    gid, ratio, exact = map_to_common(band, groups)
    return pd.Series({"age_group": gid, "exact": exact})

long = long.join(long["age_label"].apply(to_group))
print(long["age_group"].value_counts(dropna=False))
print("공통 연령대에 걸치는 라벨:", long.loc[long["exact"] == False, "age_label"].unique())

# %% 4. 확인할 질문 ── 기획서 11장 점검 항목
data = long[(long["age_group"].notna()) & (~long["age_group"].isin(["total", "unknown"]))]

# Q1. 연도 범위와 최신 연도(잠정 통계 여부)
if COLUMN["year"]:
    print("연도:", sorted(df[COLUMN["year"]].unique())[:3], "...", sorted(df[COLUMN["year"]].unique())[-3:])

# Q2. '-'(신고 대상 아님)와 0(발생 없음) 구분이 유지되는가
print("해당 없음('-'):", int(long.get("not_applicable", pd.Series(dtype=bool)).sum()))

# Q3. 연령별 합이 합계 행과 맞는가 (이중 집계 여부)
total_rows = long[long["age_group"] == "total"]
print("합계 행 수:", len(total_rows))

# Q4. 감염병별로 발생이 가장 많은 공통 연령대
top = (data.groupby([COLUMN["disease"], "age_group"])["value"].sum().reset_index()
       .sort_values("value", ascending=False).groupby(COLUMN["disease"]).head(1))
top.head(20)

# %% 5. 그림: 상위 감염병의 연령대 분포 (보고서용)
order = [g.id for g in groups]
pivot = (data.groupby([COLUMN["disease"], "age_group"])["value"].sum().unstack("age_group")
         .reindex(columns=order).fillna(0))
top10 = pivot.sum(axis=1).nlargest(10).index
share = pivot.loc[top10].div(pivot.loc[top10].sum(axis=1), axis=0)
ax = share.plot(kind="barh", stacked=True, figsize=(9, 5), colormap="viridis")
ax.set_xlabel("연령대 비중")
ax.set_ylabel("")
ax.legend(title="연령대", bbox_to_anchor=(1.01, 1), loc="upper left")
plt.tight_layout()
plt.savefig(FIGS / "age_share_top10.png", dpi=150)
