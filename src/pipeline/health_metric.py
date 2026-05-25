from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd
import yaml


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_WEIGHTS_PATH = ROOT / "configs" / "weights.yaml"


@dataclass(frozen=True)
class FeatureSpec:
    name: str
    weight: float
    direction: int


@dataclass(frozen=True)
class GroupSpec:
    name: str
    weight: float
    features: tuple[FeatureSpec, ...]


@dataclass(frozen=True)
class WeightsConfig:
    groups: tuple[GroupSpec, ...]

    @property
    def all_features(self) -> list[str]:
        return [f.name for g in self.groups for f in g.features]


@dataclass(frozen=True)
class HealthScore:
    health: float
    groups: dict[str, float]
    features: dict[str, float]
    stratified: Optional[float] = None
    drift_signal: Optional[float] = None


def load_weights(path: str | Path = DEFAULT_WEIGHTS_PATH) -> WeightsConfig:
    with open(path, encoding="utf-8") as fp:
        raw = yaml.safe_load(fp)

    groups: list[GroupSpec] = []
    for group_name, group_cfg in raw["groups"].items():
        features = tuple(
            FeatureSpec(
                name=feature_name,
                weight=float(feature_cfg["weight"]),
                direction=int(feature_cfg["direction"]),
            )
            for feature_name, feature_cfg in group_cfg["features"].items()
        )
        groups.append(
            GroupSpec(
                name=group_name,
                weight=float(group_cfg["weight"]),
                features=features,
            )
        )
    return WeightsConfig(groups=tuple(groups))


def compute_metrics(summary_df: pd.DataFrame) -> dict[str, float]:
    """Схлопывает строки day-category summary в один weighted dict метрик."""
    if summary_df is None or len(summary_df) == 0:
        return {}
    if "n_sessions" not in summary_df.columns:
        raise KeyError("day summary must contain n_sessions")

    n_sessions = int(summary_df["n_sessions"].sum())
    if n_sessions == 0:
        return {}

    def weighted_mean(column: str) -> float:
        if column not in summary_df.columns:
            return float("nan")
        mask = summary_df[column].notna()
        if not mask.any():
            return float("nan")
        weights = summary_df.loc[mask, "n_sessions"]
        return float((summary_df.loc[mask, column] * weights).sum() / weights.sum())

    metrics: dict[str, float] = {
        "n_sessions": float(n_sessions),
        "n_users": float(summary_df["n_users"].sum()) if "n_users" in summary_df.columns else float("nan"),
    }

    skip = {"date", "category", "n_users", "n_sessions", "is_cart"}
    for column in summary_df.columns:
        if column not in skip:
            metrics[column] = weighted_mean(column)

    if "is_cart" in summary_df.columns:
        metrics["session_conversion_rate"] = float(summary_df["is_cart"].sum() / n_sessions)

    return metrics


def index_metric(today: float, baseline: float, direction: int) -> float:
    if baseline is None or pd.isna(baseline) or baseline == 0:
        return float("nan")
    if today is None or pd.isna(today):
        return float("nan")
    index = today / baseline * 100.0
    return 200.0 - index if direction == -1 else index


def metric_consensus(
    today: dict[str, float],
    baseline: dict[str, float],
    weights: WeightsConfig,
) -> HealthScore:
    feature_indices: dict[str, float] = {}
    group_scores: dict[str, float] = {}

    for group in weights.groups:
        group_values: list[float] = []
        for feature in group.features:
            value = index_metric(
                today=today.get(feature.name),
                baseline=baseline.get(feature.name),
                direction=feature.direction,
            )
            feature_indices[feature.name] = value
            if not np.isnan(value):
                group_values.append(value)
        group_scores[group.name] = float(np.mean(group_values)) if group_values else float("nan")

    total_weight = sum(g.weight for g in weights.groups if not np.isnan(group_scores[g.name]))
    if total_weight == 0:
        health = float("nan")
    else:
        health = float(
            sum(group_scores[g.name] * g.weight for g in weights.groups if not np.isnan(group_scores[g.name]))
            / total_weight
        )
    return HealthScore(health=health, groups=group_scores, features=feature_indices)


def stratified_health(
    today_df: pd.DataFrame,
    baseline_df: pd.DataFrame,
    weights: WeightsConfig,
    min_sessions_per_category: int = 30,
) -> float:
    if len(today_df) == 0 or len(baseline_df) == 0:
        return float("nan")

    baseline_weights = (
        baseline_df.groupby("category")["n_sessions"].sum()
        / baseline_df["n_sessions"].sum()
    )

    weighted_scores: list[float] = []
    used_weights: list[float] = []
    for category, category_weight in baseline_weights.items():
        today_cat = today_df[today_df["category"] == category]
        base_cat = baseline_df[baseline_df["category"] == category]
        if len(today_cat) == 0 or len(base_cat) == 0:
            continue
        if int(today_cat["n_sessions"].sum()) < min_sessions_per_category:
            continue

        result = metric_consensus(compute_metrics(today_cat), compute_metrics(base_cat), weights)
        if np.isnan(result.health):
            continue
        weighted_scores.append(result.health * float(category_weight))
        used_weights.append(float(category_weight))

    if not weighted_scores:
        return float("nan")
    return float(sum(weighted_scores) / sum(used_weights))


def health_score(
    today_df: pd.DataFrame,
    baseline_df: pd.DataFrame,
    weights: WeightsConfig,
    include_stratified: bool = True,
) -> HealthScore:
    raw = metric_consensus(compute_metrics(today_df), compute_metrics(baseline_df), weights)
    if not include_stratified:
        return raw

    stratified = stratified_health(today_df, baseline_df, weights)
    drift = stratified - raw.health if not (np.isnan(stratified) or np.isnan(raw.health)) else float("nan")
    return HealthScore(
        health=raw.health,
        groups=raw.groups,
        features=raw.features,
        stratified=stratified,
        drift_signal=drift,
    )
