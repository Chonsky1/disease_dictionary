"""공공데이터 문서 (F2016 여러 종류의 문서 출처 결합, F2010 특화 기능의 근거).

위키백과(서술형 백과 문서)와 종류가 다른 두 출처를 감염병별 '레코드 문서'로 만들어 disease_docs에 함께 색인한다.

| 출처 종류 | source_type | 만드는 곳 | 문서 1건 |
| --- | --- | --- | --- |
| 공공데이터 API (질병관리청 검역관리지역정보) | public_api | data/processed/country_watch.json | 감염병 1개의 지정 국가 목록 |
| 공공 통계 (감염병포털 성별·연령별·표본감시) | public_stats | data/processed/age_stats.json | 감염병 1개 × 통계 묶음 1개의 연령·성별·유행 시기 요약 |

수집·청킹을 위키와 다르게 한 이유
- 수집: 위키는 질문마다 검색어로 찾아 LLM이 고르지만, 공공데이터는 감염병 ID로 바로 연결된 정형 자료라
  검색·선별 없이 색인 때 한꺼번에 만든다 (build_index가 매번 다시 만들어 기준일이 바뀌면 덮어씀).
- 청킹: 레코드 문서는 "누가 · 언제 기준 · 어떤 값" 이 한 덩어리여야 의미가 있다. 글자 수로 자르면 감염병 이름·기준일과
  숫자가 다른 청크로 갈라져 근거로 쓸 수 없으므로 chunk_size를 적용하지 않고 '섹션 = 청크'로 둔다 (chunking="record").
  지정 국가가 많으면 40개국씩 섹션을 나누고, 섹션마다 감염병·기준일 머리말을 반복한다.
"""
from __future__ import annotations

from data_prep.diseases import Disease
from service.age import AgeStats, profile, trend_summary
from service.countries import WatchData

COUNTRIES_PER_SECTION = 40
QUARANTINE_LABEL = "공공데이터 API · 검역관리지역"
STATS_LABEL = "공공 통계 · 감염병포털"


def quarantine_pages(watch: WatchData, by_id: dict[str, Disease], license_: str = "") -> dict[str, dict]:
    """감염병 ID → 검역관리지역 지정 국가 문서. 지정된 나라가 있는 감염병만."""
    if not watch.loaded:
        return {}
    rows: dict[str, list[tuple[str, str]]] = {}
    for c in watch.countries.values():
        for item in c.diseases:
            did = item.get("disease_id", "")
            if did in by_id:
                rows.setdefault(did, []).append((c.name_ko, item.get("designated", "")))
    pages = {}
    for did, pairs in rows.items():
        d = by_id[did]
        pairs = sorted(set(pairs))
        head = (f"{d.display_name}(제{d.grade}급)이(가) 검역관리지역으로 지정된 나라: {watch.as_of} 기준 {len(pairs)}개국. "
                "검역관리지역은 검역법에 따라 해당 감염병 발생 정보를 근거로 지정한 지역으로, 이 나라를 다녀온 뒤 "
                "의심 증상이 있으면 검역 신고 대상이다.")
        sections = []
        for i in range(0, len(pairs), COUNTRIES_PER_SECTION):
            part = pairs[i:i + COUNTRIES_PER_SECTION]
            body = ", ".join(f"{n}" + (f"(지정 {dt})" if dt else "") for n, dt in part)
            label = "지정 국가" if len(pairs) <= COUNTRIES_PER_SECTION else f"지정 국가 {i + 1}~{i + len(part)}"
            sections.append((label, f"{head}\n{label}: {body}"))
        pages[did] = {
            "doc_id": f"kdca-quarantine-{did}", "title": f"{d.display_name} 검역관리지역 지정 국가",
            "url": watch.source_url, "sections": sections, "source_type": "public_api",
            "source_label": QUARANTINE_LABEL, "license": license_, "fetched_at": watch.as_of, "chunking": "record",
        }
    return pages


def _pct(x: float) -> str:
    return f"{x * 100:.0f}%"


def stats_pages(stats: AgeStats, by_id: dict[str, Disease], license_: str = "") -> dict[str, list[dict]]:
    """감염병 ID → 연령·성별 신고 현황과 유행 시기 문서들."""
    if not stats.loaded:
        return {}
    out: dict[str, list[dict]] = {}
    for sid, aset in stats.sets.items():
        for ad in aset.diseases.values():
            if ad.disease_id not in by_id:
                continue
            d, p = by_id[ad.disease_id], profile(ad, stats.age_groups)
            if not p["known"]:
                continue
            ages = ", ".join(f"{a} {ad.count(a):,}건" for a in stats.age_groups if ad.count(a))
            text = (f"{d.display_name} 신고 현황 ({aset.label}{', 기간 ' + aset.period if aset.period else ''}; "
                    f"{aset.note}). 총 {p['total']:,}건, 연령 확인 {p['known']:,}건. "
                    f"성별: 남 {p['male']:,}건, 여 {p['female']:,}건 (남성 비율 {_pct(p['male_share'])}). "
                    f"가장 많은 연령대: {p['peak_age']} ({_pct(p['peak_share'])}). "
                    f"0~9세 비중 {_pct(p['kids_share'])}, 65세 이상 비중 {_pct(p['old_share'])}.\n"
                    f"연령대별 신고 건수: {ages}. 신고 건수이며 인구 대비 발생률이 아니다.")
            out.setdefault(ad.disease_id, []).append({
                "doc_id": f"kdca-stats-{sid}-{ad.disease_id}", "title": f"{d.display_name} 연령·성별 신고 현황",
                "url": stats.source_url, "sections": [("연령·성별", text)], "source_type": "public_stats",
                "source_label": STATS_LABEL, "license": license_, "fetched_at": stats.generated_at,
                "chunking": "record"})
    for tid, trend in stats.trends.items():
        for item in trend.items.values():
            if item.disease_id not in by_id:
                continue
            s = trend_summary(trend, item)
            peaks = [p for p in s["peaks"] if p["complete"]][-4:]
            if not s["typical_month"] and not peaks:
                continue
            d = by_id[item.disease_id]
            peak_txt = ", ".join(f"{p['key']} {p['week']}({p['month']}월)" for p in peaks) or "완전한 해 없음"
            text = (f"{d.display_name} 유행 시기 ({trend.label}, 단위: {trend.unit}). "
                    f"보통 유행하는 달: {s['typical_month'] or '확인 불가'}. 해·절기별 정점: {peak_txt}.")
            out.setdefault(item.disease_id, []).append({
                "doc_id": f"kdca-trend-{tid}-{item.disease_id}", "title": f"{d.display_name} 유행 시기",
                "url": stats.source_url, "sections": [("유행 시기", text)], "source_type": "public_stats",
                "source_label": STATS_LABEL, "license": license_, "fetched_at": stats.generated_at,
                "chunking": "record"})
    return out


def all_pages(watch: WatchData, stats: AgeStats, by_id: dict[str, Disease]) -> list[tuple[str, dict]]:
    """(감염병 ID, 문서) 목록. 이용 조건은 config/sources.json에서."""
    from data_prep.sources import license_label

    pages = [(did, p) for did, p in quarantine_pages(watch, by_id, license_label("quarantine_area")).items()]
    for did, ps in stats_pages(stats, by_id, license_label("age_sex_incidence")).items():
        pages += [(did, p) for p in ps]
    return pages


def index_public_docs(store, diseases: list[Disease], watch: WatchData, stats: AgeStats) -> dict:
    """공공데이터 문서를 색인 (같은 doc_id는 지우고 다시 넣으므로 몇 번을 실행해도 청크 수가 늘지 않음)."""
    by_id = {d.disease_id: d for d in diseases}
    pages = all_pages(watch, stats, by_id)
    keep = {(did, p["doc_id"]) for did, p in pages}
    stale = store.public_doc_pairs() - keep          # 기준일이 바뀌어 지정이 풀린 감염병 문서는 지운다
    for did, doc_id in stale:
        store.delete_doc(did, doc_id)
    chunks = sum(store.index_page(did, by_id[did].display_name, p, query="공공데이터") for did, p in pages)
    by_type: dict[str, int] = {}
    for _, p in pages:
        by_type[p["source_type"]] = by_type.get(p["source_type"], 0) + 1
    return {"docs": len(pages), "chunks": chunks, "removed": len(stale), "by_type": by_type}
