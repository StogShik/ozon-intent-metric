"""
Production raw-MAD anomaly detector с adaptive DOW-deseasonalization.

На наших данных 33-67% дисперсии rate-фич — weekly-сезонность. Простой
глобальный MAD строит коридор по смеси будний+выходных и даёт ложные
алерты на «нормальных субботах».

Подход:
- Для каждой фичи вычисляем std до и после вычитания DOW-средних. Если
  отношение `std_after/std_before < 0.70`, сезонность реальная — применяем
  deseasonalization. Иначе остаёмся на глобальной шкале (DOW-вычитание на
  фиче без сезонности только добавляет шум — например, cart_rate, km).
- В обоих случаях MAD считаем на всех n точках (без стратификации по DOW),
  иначе на n=8-9 точках per DOW MAD сам становится нестабильным.

Использование:
    detector = MadDetector.fit(baseline_df, features={'cart_rate': +1, 'dead_search_rate': -1}, k=3.0)
    is_alert = detector.check('cart_rate', today_value, today_dow)
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

import json
from pathlib import Path


@dataclass(frozen=True)
class Threshold:
    """Порог в шкале остатков. Чтобы перевести в исходную шкалу
    для конкретного DOW — `dow_means[dow] + lo / hi`."""
    lo: float
    hi: float
    median: float
    mad: float
    sigma: float
    n: int


@dataclass
class MadDetector:
    """Per-feature adaptive MAD detector.

    Для фичи с заметной DOW-сезонностью (std резко падает после вычитания
    DOW-средних) применяется deseasonalization. Для фичи без сезонности
    остаёмся на глобальной шкале — иначе DOW-вычитание только добавляет
    шум.

    Порог принятия решения: `std_after / std_before < dow_threshold`.
    На наших данных 0.70 отсекает rate-фичи (сезонные) от
    cart/conversion-фич (без сезонности).
    """
    thresholds: dict[str, Threshold]
    dow_means: dict[str, dict[int, float]]
    global_centers: dict[str, float]
    use_dow: dict[str, bool]
    directions: dict[str, int]
    k: float

    @classmethod
    def fit(
        cls,
        baseline: pd.DataFrame,
        features: dict[str, int],
        k: float = 3.5,
        dow_threshold: float = 0.70,
    ) -> "MadDetector":
        """baseline.index — DatetimeIndex; features = {name: direction (+1 | -1)}."""
        if not isinstance(baseline.index, pd.DatetimeIndex):
            raise TypeError("baseline.index must be DatetimeIndex")
        dow = baseline.index.dayofweek.to_numpy()
        thresholds: dict[str, Threshold] = {}
        dow_means: dict[str, dict[int, float]] = {}
        global_centers: dict[str, float] = {}
        use_dow: dict[str, bool] = {}
        for f, d in features.items():
            if f not in baseline.columns:
                raise KeyError(f"feature {f!r} missing in baseline")
            vals = baseline[f].to_numpy(dtype=float)
            means: dict[int, float] = {}
            for w in range(7):
                m = vals[dow == w]
                m = m[~np.isnan(m)]
                means[w] = float(np.mean(m)) if len(m) else float("nan")
            dow_means[f] = means
            mean_vec = np.array([means[int(w)] for w in dow])
            residuals = vals - mean_vec

            std_raw = float(np.nanstd(vals))
            std_resid = float(np.nanstd(residuals))
            ratio = (std_resid / std_raw) if std_raw > 0 else 1.0
            apply_dow = ratio < dow_threshold
            use_dow[f] = apply_dow

            if apply_dow:
                thresholds[f] = _mad_threshold(residuals, k=k)
                global_centers[f] = float("nan")
            else:
                center = float(np.nanmedian(vals))
                global_centers[f] = center
                thresholds[f] = _mad_threshold(vals - center, k=k)
        return cls(
            thresholds=thresholds,
            dow_means=dow_means,
            global_centers=global_centers,
            use_dow=use_dow,
            directions=dict(features),
            k=k,
        )

    def residual(self, feature: str, value: float, dow: int) -> float:
        """Возвращает (value - center). Для DOW-фич center = dow_mean; иначе center = global median."""
        if self.use_dow[feature]:
            m = self.dow_means[feature].get(int(dow))
        else:
            m = self.global_centers[feature]
        if m is None or np.isnan(m) or np.isnan(value):
            return float("nan")
        return value - m

    def check(self, feature: str, value: float, dow: int) -> bool:
        """True = алерт. Учитывает direction: anti · выход вверх, positive · вниз."""
        r = self.residual(feature, value, dow)
        if np.isnan(r):
            return False
        th = self.thresholds[feature]
        if self.directions[feature] == -1:
            return r > th.hi
        return r < th.lo

    def check_two_sided(self, feature: str, value: float, dow: int) -> bool:
        """Алерт при выходе в любую сторону (для диагностики)."""
        r = self.residual(feature, value, dow)
        if np.isnan(r):
            return False
        th = self.thresholds[feature]
        return r < th.lo or r > th.hi

    def corridor_for_dow(self, feature: str, dow: int) -> tuple[float, float]:
        """Возвращает (lo, hi) в исходной шкале для конкретного DOW."""
        if self.use_dow[feature]:
            m = self.dow_means[feature].get(int(dow))
        else:
            m = self.global_centers[feature]
        th = self.thresholds[feature]
        return m + th.lo, m + th.hi

    def save(self, path: Path) -> None:
        state = {
            "version": "v1",
            "directions": self.directions,
            "k": self.k,
            "dow_means": {
                feature: [means[i] for i in range(7)]
                for feature, means in self.dow_means.items()
            },
            "use_dow": self.use_dow,
            "thresholds": {
                feature: {
                    "lo": th.lo,
                    "hi": th.hi,
                    "median": th.median,
                    "mad": th.mad,
                    "sigma": th.sigma,
                    "n": th.n,
                }
                for feature, th in self.thresholds.items()
            },
            "global_centers": self.global_centers,
        }
        path.parent.mkdir(parents=True, exist_ok=True)
        def _nan_to_none(obj):
            import math
            if isinstance(obj, dict):
                return {k: _nan_to_none(v) for k, v in obj.items()}
            if isinstance(obj, list):
                return [_nan_to_none(v) for v in obj]
            if isinstance(obj, float) and math.isnan(obj):
                return None
            return obj
        with path.open("w", encoding="utf-8") as file:
            json.dump(_nan_to_none(state), file, ensure_ascii=False,
                      indent=2, allow_nan=False)

    @classmethod
    def load(cls, path: Path) -> "MadDetector":
        with path.open("r", encoding="utf-8") as file:
            state = json.load(file)
        if state["version"] != "v1":
            raise ValueError(f"Unsupported version: {state['version']}")
        thresholds = {
            feature: Threshold(
                lo=float(values["lo"]),
                hi=float(values["hi"]),
                median=float(values["median"]),
                mad=float(values["mad"]),
                sigma=float(values["sigma"]),
                n=int(values["n"]),
            )
            for feature, values in state["thresholds"].items()
        }
        
        detector = cls(
            thresholds=thresholds,
            dow_means={
                feature: {
                    i: (float("nan") if values[i] is None else float(values[i]))
                    for i in range(7)
                }
                for feature, values in state["dow_means"].items()
            },
            global_centers={
                feature: (float("nan") if value is None else float(value))
                for feature, value in state["global_centers"].items()
            },
            use_dow={
                feature: bool(value)
                for feature, value in state["use_dow"].items()
            },
            directions={
                k: int(v)
                for k, v in state["directions"].items()
            },
            k=float(state["k"]),
        )

        return detector

def _mad_threshold(values: np.ndarray, k: float) -> Threshold:
    v = np.asarray(values, dtype=float)
    v = v[~np.isnan(v)]
    med = float(np.median(v))
    mad = float(np.median(np.abs(v - med)))
    sigma = 1.4826 * mad
    return Threshold(lo=med - k * sigma, hi=med + k * sigma, median=med, mad=mad, sigma=sigma, n=len(v))
