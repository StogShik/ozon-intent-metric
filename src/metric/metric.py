"""
Production Health Score: day×category metric.

Single source of truth — `configs/weights.yaml`. Обоснование формул и весов —
`docs/metric_choice.md`. Anomaly detection реализован в `anomaly.py`,
дельта-скор — отдельно как diagnostic.

API:
    weights = load_weights()                         # из configs/weights.yaml
    today   = compute_metrics(today_df)              # plain agg
    base    = compute_metrics(baseline_df)
    result  = metric_consensus(today, base, weights) # HealthScore namedtuple

    # raw vs stratified одной функцией:
    res = health_score(today_df, baseline_df, weights)
    res.health           # 100 = baseline
    res.stratified       # health после нейтрализации дрейфа категорий
    res.drift_signal     # = stratified − raw

    # diagnostic delta (не production!):
    diag = DeltaScoreDiagnostic.fit(baseline_df, weights)
    delta = diag.score(today_df, prev_df)
"""
from __future__ import annotations

import warnings
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd
import yaml

CONFIG_PATH = Path(__file__).resolve().parents[2] / "configs" / "weights.yaml"


@dataclass(frozen=True)
class FeatureSpec:
    name: str
    weight: float
    direction: int


@dataclass(frozen=True)
class GroupSpec:
    name: str
    weight: float
    method: str
    features: tuple[FeatureSpec, ...]

    def feature_names(self) -> list[str]:
        return [f.name for f in self.features]


@dataclass(frozen=True)
class WeightsConfig:
    groups: tuple[GroupSpec, ...]

    @property
    def feature_directions(self) -> dict[str, int]:
        return {f.name: f.direction for g in self.groups for f in g.features}

    @property
    def all_features(self) -> list[str]:
        return [f.name for g in self.groups for f in g.features]


def load_weights(path: Path | str = CONFIG_PATH) -> WeightsConfig:
    with open(path) as fp:
        raw = yaml.safe_load(fp)
    groups = []
    for gname, gcfg in raw["groups"].items():
        features = tuple(
            FeatureSpec(name=fname, weight=fcfg["weight"], direction=fcfg["direction"])
            for fname, fcfg in gcfg["features"].items()
        )
        groups.append(
            GroupSpec(
                name=gname,
                weight=float(gcfg["weight"]),
                method=gcfg["method"],
                features=features,
            )
        )
    return WeightsConfig(groups=tuple(groups))


def compute_metrics(df: pd.DataFrame) -> dict[str, float]:
    """day_summary (rows = date × category) · плоский dict метрик.

    Агрегация: session-weighted mean, NaN-aware (пустые категории не двигают знаменатель).
    Для бинарного `session_conversion_rate` — exact ratio `sum(is_cart)/sum(n_sessions)`.
    """
    if df is None or len(df) == 0:
        return {}

    n_sessions = int(df["n_sessions"].sum())
    if n_sessions == 0:
        return {}

    def wmean(col: str) -> float:
        if col not in df.columns:
            return float("nan")
        mask = df[col].notna()
        if not mask.any():
            return float("nan")
        w = df.loc[mask, "n_sessions"]
        return float((df.loc[mask, col] * w).sum() / w.sum())

    metrics = {
        "n_sessions": n_sessions,
        "n_users": int(df["n_users"].sum()),
    }
    skip = {"date", "category", "n_users", "n_sessions", "is_cart"}
    for col in df.columns:
        if col in skip:
            continue
        metrics[col] = wmean(col)
    if "is_cart" in df.columns:
        is_cart_sum = int(df["is_cart"].sum())
        metrics["session_conversion_rate"] = is_cart_sum / n_sessions
    return metrics


@dataclass(frozen=True)
class HealthScore:
    """Результат расчёта Health Score.

    health — взвешенная сумма групповых индексов.
    groups — group_score по каждой группе (median = 100).
    features — feature_index по каждой фиче (median = 100).
    stratified — health после фиксации baseline-долей категорий (опц., если был запрошен).
    drift_signal — stratified − raw (опц.).
    """
    health: float
    groups: dict[str, float]
    features: dict[str, float]
    stratified: Optional[float] = None
    drift_signal: Optional[float] = None

    def __repr__(self) -> str:
        msg = f"HealthScore(health={self.health:.2f}"
        if self.stratified is not None:
            msg += f", stratified={self.stratified:.2f}, drift={self.drift_signal:+.2f}"
        return msg + ")"


def index_metric(today: float, baseline: float, direction: int) -> float:
    """index = (today/baseline)·100 (positive) или 200−(today/baseline)·100 (anti).

    100 = baseline. >100 — улучшение, <100 — ухудшение, симметрично для обоих типов.
    """
    if baseline is None or pd.isna(baseline) or baseline == 0:
        return float("nan")
    if today is None or pd.isna(today):
        return float("nan")
    idx = (today / baseline) * 100.0
    return (200.0 - idx) if direction == -1 else idx


def metric_consensus(
    today: dict[str, float],
    baseline: dict[str, float],
    weights: WeightsConfig,
) -> HealthScore:
    """Production-функция: считает health-композит при Equal внутри групп
    и domain priors между.

    Имя `metric_consensus` отражает методологию: consensus PC1/OptMin провалили
    bootstrap-стабильность (см. `docs/metric_choice.md`), поэтому внутри
    групп — equal (consensus от данных, а не от формулы). Между группами —
    domain priors 0.40/0.35/0.25.
    """
    feature_indices: dict[str, float] = {}
    group_scores: dict[str, float] = {}

    for g in weights.groups:
        vals = []
        for f in g.features:
            idx = index_metric(today.get(f.name), baseline.get(f.name), f.direction)
            feature_indices[f.name] = idx
            if not np.isnan(idx):
                vals.append(idx)
        group_scores[g.name] = float(np.mean(vals)) if vals else float("nan")

    total_w = sum(g.weight for g in weights.groups if not np.isnan(group_scores[g.name]))
    if total_w == 0:
        health = float("nan")
    else:
        health = sum(
            group_scores[g.name] * g.weight
            for g in weights.groups
            if not np.isnan(group_scores[g.name])
        ) / total_w
    return HealthScore(health=health, groups=group_scores, features=feature_indices)


def stratified_health(
    today_df: pd.DataFrame,
    baseline_df: pd.DataFrame,
    weights: WeightsConfig,
    min_sessions_per_cat: int = 100,
    include_records: bool = False,
):
    """Health Score со взвешиванием по baseline-долям категорий.

    Нейтрализует дрейф состава трафика: если сегодня пришло «больше тяжёлой
    электроники», raw health просядет, а stratified — нет (если движок не изменился).

    `drift_signal = stratified − raw` > 0 · сегодня тяжёлый трафик, движок работает
    лучше, чем кажется по raw.

    Если `include_records=True`, возвращается tuple (scalar, records), где
    records — список dict для каждой категории baseline'а:
        {category, base_weight, today_share, n_sessions_today, health, included}
    Это нужно для RCA: per-category contribution = base_weight * health, drift
    раскладывается как (share_today - base_weight) * health.
    """
    base_total = baseline_df["n_sessions"].sum()
    base_weights = baseline_df.groupby("category")["n_sessions"].sum() / base_total

    today_total = today_df["n_sessions"].sum() if len(today_df) > 0 else 0
    today_per_cat = today_df.groupby("category")["n_sessions"].sum() if len(today_df) > 0 else pd.Series(dtype=int)

    contribs = []
    weights_used = []
    records: list[dict] = []
    for cat, w_cat in base_weights.items():
        td = today_df[today_df["category"] == cat]
        bd = baseline_df[baseline_df["category"] == cat]
        n_today = int(td["n_sessions"].sum()) if len(td) > 0 else 0
        share_today = float(today_per_cat.get(cat, 0)) / today_total if today_total > 0 else 0.0

        if len(td) == 0 or len(bd) == 0:
            if include_records:
                records.append({
                    "category": cat,
                    "base_weight": float(w_cat),
                    "today_share": share_today,
                    "n_sessions_today": n_today,
                    "health": float("nan"),
                    "included": False,
                    "skip_reason": "missing_today" if len(td) == 0 else "missing_baseline",
                })
            continue
        if n_today < min_sessions_per_cat:
            if include_records:
                tm = compute_metrics(td)
                bm = compute_metrics(bd)
                cat_res = metric_consensus(tm, bm, weights)
                records.append({
                    "category": cat,
                    "base_weight": float(w_cat),
                    "today_share": share_today,
                    "n_sessions_today": n_today,
                    "health": float(cat_res.health),
                    "included": False,
                    "skip_reason": "low_volume",
                })
            continue

        tm = compute_metrics(td)
        bm = compute_metrics(bd)
        cat_res = metric_consensus(tm, bm, weights)
        if np.isnan(cat_res.health):
            if include_records:
                records.append({
                    "category": cat,
                    "base_weight": float(w_cat),
                    "today_share": share_today,
                    "n_sessions_today": n_today,
                    "health": float("nan"),
                    "included": False,
                    "skip_reason": "nan_health",
                })
            continue

        contribs.append(cat_res.health * w_cat)
        weights_used.append(w_cat)
        if include_records:
            records.append({
                "category": cat,
                "base_weight": float(w_cat),
                "today_share": share_today,
                "n_sessions_today": n_today,
                "health": float(cat_res.health),
                "included": True,
                "skip_reason": None,
            })

    scalar = float(sum(contribs) / sum(weights_used)) if contribs else float("nan")
    if include_records:
        return scalar, records
    return scalar


def health_score(
    today_df: pd.DataFrame,
    baseline_df: pd.DataFrame,
    weights: WeightsConfig,
    include_stratified: bool = True,
) -> HealthScore:
    """Public-API one-shot: считает raw и stratified Health Score за один вызов.

    today_df, baseline_df — day_summary-like (строки date×category).
    Stratified можно отключить для скорости (`include_stratified=False`).
    """
    today_metrics = compute_metrics(today_df)
    baseline_metrics = compute_metrics(baseline_df)
    raw = metric_consensus(today_metrics, baseline_metrics, weights)
    if not include_stratified:
        return raw
    strat = stratified_health(today_df, baseline_df, weights)
    drift = strat - raw.health if not (np.isnan(strat) or np.isnan(raw.health)) else float("nan")
    return HealthScore(
        health=raw.health,
        groups=raw.groups,
        features=raw.features,
        stratified=strat,
        drift_signal=drift,
    )


@dataclass
class DeltaScoreDiagnostic:
    """Дельта-композит как supervised summary δX в Δtarget.

    ⚠ НЕ production. См. `docs/metric_choice.md`: на 5 синтетических сценариях
    raw-MAD по фичам доминирует (recall 1.00 vs 0.10), delta слепа к step/drift,
    biased к target-фичам. Сохранена как diagnostic ("second opinion" при
    Health Score движении), не алерт-канал.

    Реализация: Ridge на нормированных Δfeatures (без таргетных фичей)
    предсказывает усреднённый z-Δtarget. Знак выравнивается по corr с таргетами
    на train.
    """
    feature_names: tuple[str, ...]
    weights: np.ndarray
    feature_means: np.ndarray
    feature_stds: np.ndarray
    threshold_lo: float
    threshold_hi: float
    threshold_median: float

    LOO_EXCLUDE: tuple[str, ...] = (
        "cart_rate", "session_conversion_rate", "km_median_time_to_cart_s",
    )
    TARGETS: tuple[str, ...] = (
        "cart_rate", "session_conversion_rate", "km_median_time_to_cart_s",
    )

    @classmethod
    def fit(
        cls,
        baseline_df: pd.DataFrame,
        weights: WeightsConfig,
        k_threshold: float = 3.0,
        ridge_alpha: float = 1.0,
    ) -> "DeltaScoreDiagnostic":
        warnings.warn(
            "DeltaScoreDiagnostic — НЕ production anomaly channel "
            "(см. docs/metric_choice.md). Используйте MadDetector из anomaly.py.",
            UserWarning,
            stacklevel=2,
        )
        from sklearn.linear_model import Ridge

        all_features = weights.all_features
        loo_features = tuple(f for f in all_features if f not in cls.LOO_EXCLUDE)

        daily = (
            baseline_df.groupby("date", group_keys=True)
            .apply(lambda g: pd.Series(compute_metrics(g)), include_groups=False)
            .reset_index()
            .set_index("date")
            .sort_index()
        )
        daily["km_speed_to_cart"] = np.where(
            daily["km_median_time_to_cart_s"] > 0,
            1.0 / daily["km_median_time_to_cart_s"],
            np.nan,
        )
        delta = daily.diff().dropna()

        X = delta[list(loo_features)].to_numpy()
        targets = ["cart_rate", "session_conversion_rate", "km_speed_to_cart"]
        Y = delta[targets].to_numpy()

        x_mean = X.mean(axis=0)
        x_std = X.std(axis=0)
        x_std = np.where(x_std == 0, 1.0, x_std)
        Xz = (X - x_mean) / x_std
        Yz = (Y - Y.mean(axis=0)) / np.where(Y.std(axis=0) == 0, 1.0, Y.std(axis=0))
        y_combo = Yz.mean(axis=1)

        ridge = Ridge(alpha=ridge_alpha).fit(Xz, y_combo)
        w = ridge.coef_
        s = Xz @ w
        if np.mean([np.corrcoef(s, Yz[:, i])[0, 1] for i in range(Yz.shape[1])]) < 0:
            w = -w
            s = -s
        med = float(np.median(s))
        mad = float(np.median(np.abs(s - med)))
        sigma = 1.4826 * mad
        return cls(
            feature_names=loo_features,
            weights=w,
            feature_means=x_mean,
            feature_stds=x_std,
            threshold_lo=med - k_threshold * sigma,
            threshold_hi=med + k_threshold * sigma,
            threshold_median=med,
        )

    def score(self, today: dict[str, float], prev: dict[str, float]) -> float:
        """Signed delta-score для одного перехода prev · today."""
        delta = np.array(
            [today.get(f, np.nan) - prev.get(f, np.nan) for f in self.feature_names],
            dtype=float,
        )
        if np.any(np.isnan(delta)):
            return float("nan")
        z = (delta - self.feature_means) / self.feature_stds
        return float(z @ self.weights)

    def is_anomaly(self, today: dict, prev: dict) -> bool:
        s = self.score(today, prev)
        if np.isnan(s):
            return False
        return s < self.threshold_lo or s > self.threshold_hi


def metric_consensus_from_dfs(
    today_df: pd.DataFrame,
    baseline_df: pd.DataFrame,
    weights: Optional[WeightsConfig] = None,
    include_stratified: bool = True,
) -> HealthScore:
    """Удобный wrapper: загружает weights, считает health (+stratified) одной командой."""
    if weights is None:
        weights = load_weights()
    return health_score(today_df, baseline_df, weights, include_stratified=include_stratified)


__all__ = [
    "FeatureSpec",
    "GroupSpec",
    "WeightsConfig",
    "HealthScore",
    "DeltaScoreDiagnostic",
    "load_weights",
    "compute_metrics",
    "index_metric",
    "metric_consensus",
    "metric_consensus_from_dfs",
    "stratified_health",
    "health_score",
]
