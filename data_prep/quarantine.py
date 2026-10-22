"""검역관리지역 정보 → 나라별 주의 감염병 (country_watch.json) 변환.

입력 두 가지를 같은 레코드로 맞춘다.
  1) Open API 오퍼레이션 2 '검역관리지역 조회 서비스(국가별 검역감염병)'
     item: ntnNm(국가), ovseaIcdKornNm(검역감염병), srvlncBgngYmd(지정일), srvlncEndYmd(해지일), useYn
  2) 파일데이터 '질병관리청_검역관리지역정보' CSV (API 승인 전·장애 시 대체)
     열: 국가, 검역감염병, 지정일자, 해지일자, 지속여부

현재 지정 중인 것만 남긴다: useYn/지속여부가 'Y'이고, 해지일이 비었거나 오늘 이후.
"""
from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from datetime import date
from pathlib import Path

from .diseases import Disease
from .names import load_aliases, match_names, name_key

SOURCE_NAME = "질병관리청 검역관리지역정보"
SOURCE_URL = "https://www.data.go.kr/data/15139251/openapi.do"

# 인터페이스 정의서의 검역감염병 코드 (icdCd)
ICD_CODES = {
    "182": "뎅기열", "172": "동물인플루엔자인체감염증", "179": "라싸열", "103": "사스", "178": "에볼라바이러스병",
    "181": "엠폭스(원숭이두창)", "176": "중동호흡기증후군", "177": "지카바이러스", "183": "치쿤구니야열",
    "180": "코로나19", "100": "콜레라", "101": "페스트", "175": "폴리오", "184": "홍역", "102": "황열",
}


# API 감염병명 → 법정감염병 목록 이름 (정규화로 안 맞는 3개). config/name_aliases.json이 있으면 그 값이 우선.
DEFAULT_ALIASES = {"사스": "중증급성호흡기증후군(SARS)", "지카바이러스": "지카바이러스감염증",
                   "코로나19": "코로나바이러스감염증-19"}


@dataclass
class QuarantineRecord:
    country: str
    disease: str
    designated: str   # YYYY-MM-DD
    released: str     # YYYY-MM-DD 또는 ""
    active_flag: str  # Y/N


def _ymd(v) -> str:
    """'2024-09-02', '20240902', '2024.09.02', '2024-09-02 00:00' → '2024-09-02'. 해석 불가면 ''."""
    s = str(v or "").strip()
    digits = re.sub(r"\D", "", s)[:8]
    return f"{digits[:4]}-{digits[4:6]}-{digits[6:8]}" if len(digits) == 8 else ""


def from_api_items(items: list[dict]) -> list[QuarantineRecord]:
    return [QuarantineRecord(country=str(i.get("ntnNm", "")).strip(), disease=str(i.get("ovseaIcdKornNm", "")).strip(),
                             designated=_ymd(i.get("srvlncBgngYmd")), released=_ymd(i.get("srvlncEndYmd")),
                             active_flag=str(i.get("useYn", "Y")).strip().upper() or "Y")
            for i in items if i.get("ntnNm") and i.get("ovseaIcdKornNm")]


def from_csv_rows(header: list[str], rows: list[list[str]]) -> list[QuarantineRecord]:
    idx = {h.strip(): i for i, h in enumerate(header)}
    need = ("국가", "검역감염병")
    if not all(n in idx for n in need):
        raise ValueError(f"CSV에 {need} 열이 없습니다. 실제 열: {header}")

    def col(r, name):
        i = idx.get(name)
        return r[i].strip() if i is not None and i < len(r) else ""

    out = []
    for r in rows:
        if col(r, "국가") and col(r, "검역감염병"):
            flag = col(r, "지속여부").upper()
            out.append(QuarantineRecord(col(r, "국가"), col(r, "검역감염병"), _ymd(col(r, "지정일자")),
                                        _ymd(col(r, "해지일자")), "Y" if flag in ("", "Y", "예", "지속") else "N"))
    return out


def is_active(rec: QuarantineRecord, today: date) -> bool:
    if rec.active_flag != "Y":
        return False
    return not rec.released or rec.released >= today.isoformat()


def load_country_codes(path: Path) -> dict[str, str]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return {name_key(k): v for k, v in data.items() if not k.startswith("_")}


def build_watch(records: list[QuarantineRecord], diseases: list[Disease], country_codes: dict[str, str],
                alias_path: Path, today: date, source_label: str) -> tuple[dict, dict]:
    """(country_watch dict, 보고용 통계). 지도 코드가 없는 나라도 이름 키로 남겨 목록에서 고를 수 있게 한다."""
    active = [r for r in records if is_active(r, today)]
    by_display = {d.display_name: d for d in diseases}
    aliases = {name_key(k): v for k, v in DEFAULT_ALIASES.items()} | load_aliases(alias_path)
    matched, unmatched_d = match_names({r.disease for r in active}, list(by_display), aliases)

    countries: dict[str, dict] = {}
    unmapped_countries: set[str] = set()
    for r in sorted(active, key=lambda x: (x.country, x.disease)):
        iso3 = country_codes.get(name_key(r.country), "")
        if not iso3:
            unmapped_countries.add(r.country)
        key = iso3 or f"NAME:{r.country}"
        entry = countries.setdefault(key, {"name_ko": r.country, "iso3": iso3, "diseases": []})
        d = by_display.get(matched.get(r.disease, ""))
        item = {"name": d.display_name if d else r.disease, "api_name": r.disease,
                "disease_id": d.disease_id if d else "", "designated": r.designated, "released": r.released,
                "note": f"{r.designated} 지정" if r.designated else ""}
        if not any(x["api_name"] == item["api_name"] for x in entry["diseases"]):
            entry["diseases"].append(item)

    watch = {"as_of": today.isoformat(), "source": SOURCE_NAME, "source_url": SOURCE_URL,
             "collected_from": source_label, "license": "이용허락범위 제한 없음", "countries": countries}
    stats = {"records": len(records), "active": len(active), "countries": len(countries),
             "mapped": sum(1 for c in countries.values() if c["iso3"]),
             "unmapped_countries": sorted(unmapped_countries),
             "unmatched_diseases": [u["name"] for u in unmatched_d],
             "diseases": sorted({r.disease for r in active})}
    return watch, stats


def save_watch(path: Path, watch: dict) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(watch, ensure_ascii=False, indent=2), encoding="utf-8")


def records_to_json(records: list[QuarantineRecord]) -> list[dict]:
    return [asdict(r) for r in records]
