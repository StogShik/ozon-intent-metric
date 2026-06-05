from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path


@dataclass
class HealthScore:
    raw: float
    stratified: float


@dataclass
class NewDayResult:
    date: date
    health: HealthScore
    alerts: dict[str, bool]
    drift_signal: float


class metricFramework:
    def __init__(
        self,
        *,
        baseline_period: tuple[date, date],
        events_root: Path = Path("data/user_actions_3_months"),
        products_root: Path = Path("data/product_information"),
        summaries_dir: Path = Path("data/daily_summaries"),
        sessions_dir: Path = Path("data/sessions"),
        query_features_path: Path = Path("data/query_features.parquet"),
        weights_path: Path = Path("configs/weights.yaml"),
        state_dir: Path = Path("data/framework_state"),
        gap_min: int = 30,
        mad_k: float = 3.5,
    ) -> None:
        self.baseline_period = baseline_period
        self.events_root = events_root
        self.products_root = products_root
        self.summaries_dir = summaries_dir
        self.sessions_dir = sessions_dir
        self.query_features_path = query_features_path
        self.weights_path = weights_path
        self.state_dir = state_dir
        self.gap_min = gap_min
        self.mad_k = mad_k

        self.baseline_df = None
        self.detector = None
        self.baseline_categories = None
