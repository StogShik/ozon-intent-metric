from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from metric.anomaly import MadDetector


@pytest.fixture
def baseline_frame() -> pd.DataFrame:
    rng = np.random.default_rng(42)
    idx = pd.date_range("2024-01-01", periods=56, freq="D")
    weekend = np.isin(idx.dayofweek, [5, 6]).astype(float)
    return pd.DataFrame(
        {
            "seasonal": 10.0 + 3.0 * weekend + rng.normal(0, 0.05, len(idx)),
            "flat": 5.0 + rng.normal(0, 0.05, len(idx)),
            "dead_like": 0.2 + rng.normal(0, 0.005, len(idx)),
        },
        index=idx,
    )


@pytest.fixture
def detector(baseline_frame) -> MadDetector:
    return MadDetector.fit(
        baseline_frame,
        features={"seasonal": +1, "flat": +1, "dead_like": -1},
        k=3.5,
    )


def test_adaptive_dow_selection(detector):
    assert detector.use_dow["seasonal"] is True
    assert detector.use_dow["flat"] is False
    assert detector.use_dow["dead_like"] is False


def test_dow_deseasonalization_no_false_alert_on_weekend(detector):
    assert detector.check("seasonal", 13.0, dow=5) is False
    assert detector.check("seasonal", 10.0, dow=5) is True


def test_direction_awareness(detector):
    assert detector.check("flat", 4.0, dow=2) is True
    assert detector.check("flat", 6.0, dow=2) is False
    assert detector.check("dead_like", 0.35, dow=2) is True
    assert detector.check("dead_like", 0.05, dow=2) is False


def test_two_sided_check(detector):
    assert detector.check_two_sided("flat", 6.0, dow=2) is True
    assert detector.check_two_sided("flat", 5.0, dow=2) is False


def test_nan_value_never_alerts(detector):
    assert detector.check("flat", float("nan"), dow=2) is False


def test_corridor_for_dow_brackets_center(detector):
    lo, hi = detector.corridor_for_dow("seasonal", 5)
    assert lo < 13.0 < hi
    lo_wd, hi_wd = detector.corridor_for_dow("seasonal", 1)
    assert lo_wd < 10.0 < hi_wd
    assert hi_wd < lo


def test_save_load_roundtrip(detector, baseline_frame, tmp_path):
    path = tmp_path / "state" / "mad_detector.json"
    detector.save(path)
    loaded = MadDetector.load(path)

    assert loaded.k == detector.k
    assert loaded.use_dow == detector.use_dow
    assert loaded.directions == detector.directions
    for f, th in detector.thresholds.items():
        assert loaded.thresholds[f].lo == pytest.approx(th.lo)
        assert loaded.thresholds[f].hi == pytest.approx(th.hi)
        assert loaded.thresholds[f].sigma == pytest.approx(th.sigma)

    rng = np.random.default_rng(0)
    for f in detector.thresholds:
        for v in rng.uniform(0, 20, 40):
            for dow in range(7):
                assert loaded.check(f, float(v), dow) == detector.check(f, float(v), dow)


def test_fit_requires_datetime_index(baseline_frame):
    with pytest.raises(TypeError):
        MadDetector.fit(baseline_frame.reset_index(drop=True), {"flat": +1})


def test_fit_unknown_feature_raises(baseline_frame):
    with pytest.raises(KeyError):
        MadDetector.fit(baseline_frame, {"no_such_column": +1})
