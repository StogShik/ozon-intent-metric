"""
RCA (root-cause analysis) для Health Score.

Два контура:
- **Temporal** (decompose_health, top_contributors, explain_drop, anomaly_summary,
  category_drilldown). Отвечает на «Health упал в день D — почему».
  Аддитивная декомпозиция: Σ feature_contrib = health, Σ base_weight · health_c =
  stratified.
- **Structural** (segment_breakdown, weak_spots, segment_day_view,
  example_failures). Отвечает на «где движок системно проседает по NLP-срезам:
  опечатки, латиница, длина запроса, число уточнений». Работает на per-session
  данных (intent_sessions_with_query_features.parquet). Срезы считаются в двух
  гранулярностях: за весь период (`date="ALL"`) и по дням (`date="YYYY-MM-DD"`),
  что позволяет связать просадку конкретного дня с конкретным сегментом
  (`segment_day_view`: Δ success_rate дня против нормы сегмента за период).

Функции — pure, не пишут на диск; вызываются из run_daily_pipeline.py для
precompute и из web/server.py при ответе на запросы.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Iterable, Optional

import numpy as np
import pandas as pd

from metric.anomaly import MadDetector
from metric.metric import (
    HealthScore,
    WeightsConfig,
    compute_metrics,
    health_score,
    index_metric,
    metric_consensus,
    stratified_health,
)


DECOMP_COLUMNS: tuple[str, ...] = (
    "date", "level", "group", "feature", "category",
    "today_value", "baseline_value", "direction",
    "index_value", "contribution",
    "is_anomaly", "dow_used", "residual", "sigma",
    "corridor_lo", "corridor_hi",
    "n_sessions_today", "base_weight", "today_share",
    "included", "skip_reason",
)


def decompose_health(
    today_df: pd.DataFrame,
    baseline_df: pd.DataFrame,
    weights: WeightsConfig,
    day: date | str,
    detector: Optional[MadDetector] = None,
    min_sessions_per_cat: int = 100,
) -> pd.DataFrame:
    """Полная разложение Health Score за один день в long-format.

    Возвращает DataFrame со строками трёх уровней:
    - **group** (3 rows): group_score + signed contribution к health.
    - **feature** (16 rows): feature_index + contribution + anomaly-метки (если
      передан detector).
    - **category** (~30 rows): per-category health + contribution к stratified.

    Инварианты:
        Σ contribution[level='feature'] ≈ HealthScore.health (raw)
        Σ contribution[level='group']   ≈ HealthScore.health (raw)
        Σ contribution[level='category', included=True] ≈ HealthScore.stratified

    Если фича NaN сегодня — она исключается из group_score (NaN-aware mean),
    но строка остаётся в decomposition с `index_value=NaN, contribution=NaN`.
    Категория с `n_sessions_today < min_sessions_per_cat` либо отсутствующая в
    baseline получает строку с `included=False, skip_reason=...`.
    """
    iso_day = day.isoformat() if isinstance(day, date) else str(day)

    today_metrics = compute_metrics(today_df)
    baseline_metrics = compute_metrics(baseline_df)

    feature_rows: list[dict[str, Any]] = []
    group_score_per_group: dict[str, float] = {}
    nonnan_count_per_group: dict[str, int] = {}

    for g in weights.groups:
        vals: list[float] = []
        for f in g.features:
            today_v = today_metrics.get(f.name)
            base_v = baseline_metrics.get(f.name)
            idx = index_metric(today_v, base_v, f.direction)
            if not np.isnan(idx):
                vals.append(idx)
            feature_rows.append({
                "level": "feature",
                "group": g.name,
                "feature": f.name,
                "category": None,
                "today_value": _as_float(today_v),
                "baseline_value": _as_float(base_v),
                "direction": int(f.direction),
                "index_value": _as_float(idx),
                "_g_weight": float(g.weight),
            })
        group_score_per_group[g.name] = float(np.mean(vals)) if vals else float("nan")
        nonnan_count_per_group[g.name] = len(vals)

    total_w = sum(g.weight for g in weights.groups if not np.isnan(group_score_per_group[g.name]))
    if total_w == 0:
        raw_health = float("nan")
    else:
        raw_health = sum(
            group_score_per_group[g.name] * g.weight
            for g in weights.groups
            if not np.isnan(group_score_per_group[g.name])
        ) / total_w

    group_rows: list[dict[str, Any]] = []
    for g in weights.groups:
        gs = group_score_per_group[g.name]
        if total_w > 0 and not np.isnan(gs):
            contrib = (g.weight / total_w) * gs
        else:
            contrib = float("nan")
        group_rows.append({
            "level": "group",
            "group": g.name,
            "feature": None,
            "category": None,
            "today_value": None,
            "baseline_value": None,
            "direction": None,
            "index_value": _as_float(gs),
            "contribution": _as_float(contrib),
            "n_features_used": nonnan_count_per_group[g.name],
        })

    for row in feature_rows:
        g_name = row["group"]
        g_weight = row.pop("_g_weight")
        nonnan = nonnan_count_per_group[g_name]
        idx = row["index_value"]
        if total_w > 0 and nonnan > 0 and not np.isnan(idx):
            row["contribution"] = (g_weight / total_w) * (1.0 / nonnan) * idx
        else:
            row["contribution"] = float("nan")

    if detector is not None:
        ts = pd.Timestamp(iso_day) if isinstance(iso_day, str) else pd.Timestamp(day)
        dow = int(ts.dayofweek)
        for row in feature_rows:
            fname = row["feature"]
            if fname not in detector.thresholds:
                continue
            today_v = row["today_value"]
            if today_v is None or np.isnan(today_v):
                continue
            row["is_anomaly"] = bool(detector.check(fname, today_v, dow))
            row["dow_used"] = bool(detector.use_dow.get(fname, False))
            row["residual"] = _as_float(detector.residual(fname, today_v, dow))
            row["sigma"] = float(detector.thresholds[fname].sigma)
            lo, hi = detector.corridor_for_dow(fname, dow)
            row["corridor_lo"] = _as_float(lo)
            row["corridor_hi"] = _as_float(hi)

    _, cat_records = stratified_health(
        today_df, baseline_df, weights,
        min_sessions_per_cat=min_sessions_per_cat,
        include_records=True,
    )

    sum_included_base_weight = sum(r["base_weight"] for r in cat_records if r["included"])
    category_rows: list[dict[str, Any]] = []
    for r in cat_records:
        if r["included"] and sum_included_base_weight > 0:
            contrib = r["base_weight"] * r["health"] / sum_included_base_weight
        else:
            contrib = float("nan")
        category_rows.append({
            "level": "category",
            "group": None,
            "feature": None,
            "category": r["category"],
            "today_value": None,
            "baseline_value": None,
            "direction": None,
            "index_value": _as_float(r["health"]),
            "contribution": _as_float(contrib),
            "n_sessions_today": int(r["n_sessions_today"]),
            "base_weight": float(r["base_weight"]),
            "today_share": float(r["today_share"]),
            "included": bool(r["included"]),
            "skip_reason": r.get("skip_reason"),
        })

    all_rows = group_rows + feature_rows + category_rows
    df = pd.DataFrame(all_rows)
    df.insert(0, "date", iso_day)

    for col in DECOMP_COLUMNS:
        if col not in df.columns:
            df[col] = None
    return df[list(DECOMP_COLUMNS)]


def _as_float(x: Any) -> float | None:
    if x is None:
        return None
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v


def load_decomposition(path: str | Path) -> pd.DataFrame:
    df = pd.read_parquet(path)
    if not pd.api.types.is_string_dtype(df["date"]):
        df["date"] = df["date"].astype(str)
    return df


def top_contributors(
    decomp: pd.DataFrame,
    day: date | str,
    level: str = "feature",
    n: int = 10,
    direction: str = "worst",
) -> list[dict[str, Any]]:
    """Top-N контрибьюторов в Health Score на данный день."""
    iso = day.isoformat() if isinstance(day, date) else str(day)
    sub = decomp[(decomp["date"] == iso) & (decomp["level"] == level)].copy()
    sub = sub[~sub["contribution"].isna()]
    ascending = (direction == "worst")
    sub = sub.sort_values("contribution", ascending=ascending).head(n)
    return sub.to_dict(orient="records")


def explain_drop(
    decomp: pd.DataFrame,
    date_from: date | str,
    date_to: date | str,
    top_n: int = 10,
) -> dict[str, Any]:
    """Раскладывает Δhealth = health(to) − health(from) по группам/фичам/категориям.

    Каждая «дельта-строка»:
        delta_contribution = contribution_to − contribution_from
    Сумма по level='feature' ≈ Δhealth (raw).

    Возвращает:
        {date_from, date_to, delta_health_raw, delta_health_stratified,
         by_group: [...], by_feature_top: [...], by_category_top: [...],
         caveats: [...]}
    """
    a = date_from.isoformat() if isinstance(date_from, date) else str(date_from)
    b = date_to.isoformat() if isinstance(date_to, date) else str(date_to)

    df_a = decomp[decomp["date"] == a]
    df_b = decomp[decomp["date"] == b]

    if df_a.empty or df_b.empty:
        return {
            "date_from": a, "date_to": b,
            "delta_health_raw": float("nan"),
            "delta_health_stratified": float("nan"),
            "by_group": [], "by_feature_top": [], "by_category_top": [],
            "caveats": [f"date {a!r} or {b!r} missing in decomposition"],
        }

    raw_a = df_a[df_a["level"] == "feature"]["contribution"].sum(skipna=True)
    raw_b = df_b[df_b["level"] == "feature"]["contribution"].sum(skipna=True)
    strat_a = df_a[(df_a["level"] == "category") & (df_a["included"])]["contribution"].sum(skipna=True)
    strat_b = df_b[(df_b["level"] == "category") & (df_b["included"])]["contribution"].sum(skipna=True)

    by_group = _delta_join(df_a, df_b, "level", "group", ["group"])
    by_feature = _delta_join(df_a, df_b, "level", "feature", ["group", "feature"])
    by_category = _delta_join(df_a, df_b, "level", "category", ["category"])

    by_group.sort(key=lambda r: r["delta_contribution"] if r["delta_contribution"] is not None else 0.0)
    by_feature.sort(key=lambda r: r["delta_contribution"] if r["delta_contribution"] is not None else 0.0)
    by_category.sort(key=lambda r: r["delta_contribution"] if r["delta_contribution"] is not None else 0.0)

    caveats: list[str] = []
    def _missing(v: Any) -> bool:
        return v is None or (isinstance(v, float) and np.isnan(v))
    flipped = [r for r in by_feature
               if _missing(r["from_index"]) ^ _missing(r["to_index"])]
    if flipped:
        caveats.append(
            f"{len(flipped)} feature(s) flipped NaN status between {a} and {b}; "
            "their Δcontribution is partly structural (group divisor changed)."
        )

    return {
        "date_from": a, "date_to": b,
        "delta_health_raw": float(raw_b - raw_a) if not np.isnan(raw_b - raw_a) else float("nan"),
        "delta_health_stratified": float(strat_b - strat_a) if not np.isnan(strat_b - strat_a) else float("nan"),
        "by_group": by_group,
        "by_feature_top": by_feature[:top_n],
        "by_category_top": by_category[:top_n],
        "caveats": caveats,
    }


def _delta_join(
    df_a: pd.DataFrame,
    df_b: pd.DataFrame,
    level_col: str,
    level_value: str,
    keys: list[str],
) -> list[dict[str, Any]]:
    a = df_a[df_a[level_col] == level_value][keys + ["index_value", "contribution"]].copy()
    b = df_b[df_b[level_col] == level_value][keys + ["index_value", "contribution"]].copy()
    a = a.rename(columns={"index_value": "from_index", "contribution": "from_contribution"})
    b = b.rename(columns={"index_value": "to_index", "contribution": "to_contribution"})
    m = pd.merge(a, b, on=keys, how="outer")
    m["delta_contribution"] = m["to_contribution"] - m["from_contribution"]
    m["delta_index"] = m["to_index"] - m["from_index"]
    result = []
    for _, row in m.iterrows():
        rec: dict[str, Any] = {k: row[k] for k in keys}
        rec.update({
            "from_index": _as_float(row["from_index"]),
            "to_index": _as_float(row["to_index"]),
            "from_contribution": _as_float(row["from_contribution"]),
            "to_contribution": _as_float(row["to_contribution"]),
            "delta_contribution": _as_float(row["delta_contribution"]),
            "delta_index": _as_float(row["delta_index"]),
        })
        result.append(rec)
    return result


def anomaly_summary(decomp: pd.DataFrame, day: date | str) -> list[dict[str, Any]]:
    """Все фичи, которые сработали как аномалия на этот день."""
    iso = day.isoformat() if isinstance(day, date) else str(day)
    sub = decomp[
        (decomp["date"] == iso)
        & (decomp["level"] == "feature")
        & (decomp["is_anomaly"] == True)
    ]
    return sub.to_dict(orient="records")


def category_drilldown(
    decomp: pd.DataFrame,
    day: date | str,
    category: str,
) -> dict[str, Any]:
    """Возвращает запись категории за день с метаданными.

    Note: per-feature breakdown ВНУТРИ категории сейчас не в decomposition.parquet
    (это бы добавило N_categories × 16 строк в день). UI может либо запросить
    дополнительно (новый endpoint, не precomputed), либо показывать только
    aggregate-уровень категории.
    """
    iso = day.isoformat() if isinstance(day, date) else str(day)
    sub = decomp[
        (decomp["date"] == iso)
        & (decomp["level"] == "category")
        & (decomp["category"] == category)
    ]
    if sub.empty:
        return {"date": iso, "category": category, "found": False}
    rec = sub.iloc[0].to_dict()
    rec["found"] = True
    return rec


DEFAULT_SEGMENTS: dict[str, str] = {
    "is_in_product_base": "categorical:is_in_product_base",
    "is_article": "categorical:is_article",
    "query_stratification_score": "categorical:query_stratification_score",
    "query_len_bucket": "bucket:query_len_words:1,2,3,4,6",
    "n_unique_queries_bucket": "bucket:n_unique_queries:1,2,3,5",
    "n_view_bucket": "bucket:n_view:0,1,3,10",
    "first_widget": "categorical:first_widget",
    "top_category_in_session": "categorical:top_category_in_session",
}


def segment_value(sessions: pd.DataFrame, segment_name: str) -> pd.Series:
    """Возвращает Series — значение сегмента для каждой строки sessions."""
    spec = DEFAULT_SEGMENTS.get(segment_name)
    if spec is None:
        if segment_name in sessions.columns:
            return sessions[segment_name].astype("string").fillna("(null)")
        raise KeyError(f"unknown segment: {segment_name!r}")

    kind, *rest = spec.split(":")
    if kind == "categorical":
        col = rest[0]
        if col not in sessions.columns:
            return pd.Series(["(missing)"] * len(sessions), index=sessions.index, dtype="string")
        return sessions[col].astype("string").fillna("(null)")

    if kind == "bucket":
        col, edges_str = rest[0], rest[1]
        edges = [int(x) for x in edges_str.split(",")]
        if col not in sessions.columns:
            return pd.Series(["(missing)"] * len(sessions), index=sessions.index, dtype="string")
        values = sessions[col].fillna(-1).astype(int)
        return values.apply(lambda v: _bucket(v, edges)).astype("string")

    raise ValueError(f"unknown segment spec kind: {kind!r}")


def _bucket(v: int, edges: list[int]) -> str:
    if v < 0:
        return "(null)"
    if v < edges[0]:
        return f"<{edges[0]}"
    for i in range(len(edges) - 1):
        if edges[i] <= v < edges[i + 1]:
            if edges[i + 1] - edges[i] == 1:
                return str(edges[i])
            return f"{edges[i]}-{edges[i + 1] - 1}"
    return f"{edges[-1]}+"


def segment_breakdown(
    sessions: pd.DataFrame,
    segment_name: str,
    success_col: str = "reached_cart",
    by_date: bool = False,
    date_col: str = "ts_end",
) -> pd.DataFrame:
    """По сегменту возвращает per-value: n_sessions, success_rate, медианы.

    Колонки выхода: segment, value, date, n_sessions, n_success, success_rate,
    share, lift_vs_overall, failure_volume, median_n_queries, median_n_views,
    median_time_s.

    `by_date=False` — один срез за весь период, `date="ALL"`.
    `by_date=True` — срез на каждый день (`date = ts_end.date()`, как в
    day_summary); `share` и `lift_vs_overall` считаются внутри дня.
    """
    if success_col not in sessions.columns:
        raise KeyError(f"success column {success_col!r} missing from sessions")

    s = segment_value(sessions, segment_name)
    df = sessions.assign(__seg=s)
    if by_date:
        if date_col not in sessions.columns:
            raise KeyError(f"date column {date_col!r} missing from sessions")
        df = df.assign(__date=df[date_col].dt.strftime("%Y-%m-%d"))
    else:
        df = df.assign(__date="ALL")
    grouped = df.groupby(["__date", "__seg"], dropna=False)

    aggs: dict[str, tuple[str, str]] = {
        "n_sessions": (success_col, "size"),
        "n_success": (success_col, "sum"),
    }
    for opt_col, alias in [
        ("n_unique_queries", "median_n_queries"),
        ("n_view", "median_n_views"),
        ("duration_s", "median_time_s"),
    ]:
        if opt_col in sessions.columns:
            aggs[alias] = (opt_col, "median")
    out = grouped.agg(**aggs).reset_index().rename(columns={"__seg": "value", "__date": "date"})

    denom = df.groupby("__date")[success_col].agg(["mean", "size"])
    denom.columns = ["_overall_sr", "_total_n"]
    out = out.merge(denom, left_on="date", right_index=True, how="left")

    out["segment"] = segment_name
    out["success_rate"] = out["n_success"] / out["n_sessions"]
    out["share"] = out["n_sessions"] / out["_total_n"]
    out["lift_vs_overall"] = out["success_rate"] - out["_overall_sr"]
    fv = (out["n_sessions"] * (1 - out["success_rate"])).round().fillna(0).astype(int)
    out["failure_volume"] = fv
    cols_required = ["segment", "value", "date", "n_sessions", "n_success", "success_rate",
                     "share", "lift_vs_overall", "failure_volume"]
    cols_optional = ["median_n_queries", "median_n_views", "median_time_s"]
    cols = cols_required + [c for c in cols_optional if c in out.columns]
    return (out[cols]
            .sort_values(["date", "failure_volume"], ascending=[True, False])
            .reset_index(drop=True))


def weak_spots(
    breakdowns: pd.DataFrame,
    min_volume: int = 200,
    min_lift_threshold: float = -0.05,
    top_n: int = 20,
) -> pd.DataFrame:
    """Ранжированный список «слабых мест» = (segment, value) с худшим success_rate.

    Параметры:
    - min_volume — минимальный объём сессий, чтобы значение попало в выдачу
      (отсекает редкие хвосты).
    - min_lift_threshold — отсекает «обычные» сегменты. Значение −0.05 = берём
      только те, что хуже общего success_rate как минимум на 5 п.п.
    - top_n — сколько вернуть.

    Сортировка по failure_volume DESC (= ваш ROI приоритет: «фиксим самый
    большой пул провальных сессий»).
    """
    if breakdowns.empty or "n_sessions" not in breakdowns.columns:
        return breakdowns.head(0)
    if "date" in breakdowns.columns:
        breakdowns = breakdowns[breakdowns["date"] == "ALL"]
    df = breakdowns[
        (breakdowns["n_sessions"] >= min_volume)
        & (breakdowns["lift_vs_overall"] <= min_lift_threshold)
    ]
    return df.sort_values("failure_volume", ascending=False).head(top_n).reset_index(drop=True)


def precompute_all_segments(
    sessions: pd.DataFrame,
    segments: Iterable[str] | None = None,
    include_daily: bool = False,
) -> pd.DataFrame:
    """Запускает segment_breakdown для всех segments, конкатенирует.

    `include_daily=True` дополнительно кладёт per-day строки (date="YYYY-MM-DD")
    рядом с агрегатом периода (date="ALL") — они нужны для temporal-привязки
    weak-spots («какой сегмент просел именно в этот день», segment_day_view).
    """
    if segments is None:
        segments = DEFAULT_SEGMENTS.keys()
    parts = []
    for seg in segments:
        try:
            parts.append(segment_breakdown(sessions, seg))
        except KeyError:
            continue
        if include_daily:
            try:
                parts.append(segment_breakdown(sessions, seg, by_date=True))
            except KeyError:
                continue
    if not parts:
        return pd.DataFrame()
    return pd.concat(parts, ignore_index=True)


def segment_day_view(
    breakdowns: pd.DataFrame,
    day: date | str,
    min_volume: int = 200,
    top_n: int = 20,
    baseline_period: tuple[str, str] | None = None,
) -> pd.DataFrame:
    """Day-mode weak spots: каждый (segment, value) за день против своей нормы.

    Норма сегмента:
    - если задан `baseline_period=(start_iso, end_iso)` и в breakdowns есть
      per-day строки этого окна — взвешенный success_rate сегмента по
      baseline-дням (Σ n_success / Σ n_sessions). Та же логика «сравниваем с
      baseline», что у самого Health Score; деградированные дни вне baseline
      норму не разводняют.
    - иначе fallback: агрегат всего периода (строки date="ALL").

    Ключевые колонки выхода:
    - `success_rate` / `success_rate_period` — день vs норма;
    - `delta_vs_period` — разница (отрицательная = сегмент просел);
    - `extra_failures` — n_sessions_day × (norm − sr_day), «лишние»
      провалы дня относительно нормы. Сортировка по ним DESC = главный
      ответ на «почему именно сегодня плохо»;
    - `norm_source` — "baseline" или "period" (что фактически использовано).

    Требует per-day строк в breakdowns (precompute_all_segments(include_daily=True)).
    Если их нет (старый segment_breakdown.parquet) — возвращает пустой DataFrame.
    """
    if breakdowns.empty or "date" not in breakdowns.columns:
        return pd.DataFrame()
    iso = day.isoformat() if isinstance(day, date) else str(day)
    day_rows = breakdowns[breakdowns["date"] == iso]
    if day_rows.empty:
        return pd.DataFrame()

    norm = pd.DataFrame()
    norm_source = "period"
    if baseline_period is not None:
        lo, hi = str(baseline_period[0]), str(baseline_period[1])
        base = breakdowns[
            (breakdowns["date"] != "ALL")
            & (breakdowns["date"] >= lo)
            & (breakdowns["date"] <= hi)
        ]
        if not base.empty:
            norm = (
                base.groupby(["segment", "value"], dropna=False)
                .agg(_succ=("n_success", "sum"), n_sessions_period=("n_sessions", "sum"))
                .reset_index()
            )
            norm["success_rate_period"] = norm["_succ"] / norm["n_sessions_period"]
            norm = norm.drop(columns=["_succ"])
            norm_source = "baseline"
    if norm.empty:
        norm = breakdowns.loc[
            breakdowns["date"] == "ALL",
            ["segment", "value", "success_rate", "n_sessions"],
        ].rename(columns={
            "success_rate": "success_rate_period",
            "n_sessions": "n_sessions_period",
        })

    m = day_rows.merge(norm, on=["segment", "value"], how="left")
    m = m[m["n_sessions"] >= min_volume]
    m["delta_vs_period"] = m["success_rate"] - m["success_rate_period"]
    extra = (m["n_sessions"] * (-m["delta_vs_period"])).round()
    m["extra_failures"] = extra.fillna(0).astype(int)
    m["norm_source"] = norm_source
    return (m.sort_values("extra_failures", ascending=False)
            .head(top_n)
            .reset_index(drop=True))


def example_failures(
    sessions: pd.DataFrame,
    segment_name: str,
    value: str,
    n: int = 10,
    columns: list[str] | None = None,
    day: date | str | None = None,
) -> list[dict[str, Any]]:
    """Возвращает n примеров провальных сессий (reached_cart=False) для среза.

    `day` (опционально) ограничивает примеры одним днём (`ts_end` внутри дня) —
    чтобы смотреть провалы именно той даты, где увидели просадку.

    user_id намеренно НЕ включён в default columns — это PII и для UI-демо
    не нужен. session_idx достаточно для идентификации в дебаг-целях.
    """
    s = segment_value(sessions, segment_name)
    mask = (s == value) & (~sessions["reached_cart"].astype(bool))
    if day is not None and "ts_end" in sessions.columns:
        d0 = pd.Timestamp(day.isoformat() if isinstance(day, date) else str(day))
        mask &= (sessions["ts_end"] >= d0) & (sessions["ts_end"] < d0 + pd.Timedelta(days=1))
    sub = sessions[mask]
    if columns is None:
        columns = [c for c in [
            "session_idx", "ts_start", "ts_end", "first_query", "query_clean",
            "n_unique_queries", "n_view", "n_click", "duration_s",
            "top_category_in_session", "first_widget",
            "query_stratification_score", "query_len_words",
            "is_in_product_base", "is_article",
        ] if c in sub.columns]
    sample = sub.head(n)
    if sample.empty:
        return []
    return sample[columns].to_dict(orient="records")
