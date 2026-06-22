from __future__ import annotations

import argparse
import functools
import json
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pandas as pd


ROOT = Path(__file__).resolve().parents[2]
WEB_DIR = Path(__file__).resolve().parent

sys.path.insert(0, str(ROOT / "src"))
from metric.rca import (
    anomaly_summary,
    category_drilldown,
    example_failures,
    explain_drop,
    load_decomposition,
    segment_day_view,
    top_contributors,
    weak_spots,
)


DEFAULT_METRICS = ROOT / "data" / "daily_metrics_full.parquet"
DEFAULT_DECOMP = ROOT / "data" / "decomposition_full.parquet"
DEFAULT_SEGMENTS = ROOT / "data" / "segment_breakdown.parquet"
DEFAULT_SESSIONS = ROOT / "data" / "intent_sessions_with_query_features.parquet"


def _json_default(value):
    if value is None:
        return None
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    if hasattr(value, "item"):
        return value.item()
    if isinstance(value, (Path,)):
        return str(value)
    return str(value)


def _sanitize_nan(obj):
    import math
    if isinstance(obj, dict):
        return {k: _sanitize_nan(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_sanitize_nan(v) for v in obj]
    if isinstance(obj, float) and math.isnan(obj):
        return None
    if hasattr(obj, "item") and hasattr(obj, "dtype"):
        try:
            v = obj.item()
            if isinstance(v, float) and math.isnan(v):
                return None
            return v
        except (TypeError, ValueError):
            return obj
    return obj


@functools.lru_cache(maxsize=4)
def _cached_read_parquet(path_str: str, mtime: float) -> pd.DataFrame:
    return pd.read_parquet(path_str)


def _read_parquet_cached(path: Path) -> pd.DataFrame:
    if not path.exists():
        raise FileNotFoundError(str(path))
    mtime = path.stat().st_mtime
    return _cached_read_parquet(str(path), mtime)


def _read_metrics(path: Path) -> pd.DataFrame:
    df = _read_parquet_cached(path)
    return df.assign(date=pd.to_datetime(df["date"]).dt.strftime("%Y-%m-%d")).sort_values("date")


def _read_decomposition(path: Path) -> pd.DataFrame:
    df = _read_parquet_cached(path)
    if not pd.api.types.is_string_dtype(df["date"]):
        df = df.assign(date=df["date"].astype(str))
    return df


def _read_segments(path: Path) -> pd.DataFrame:
    return _read_parquet_cached(path)


def _read_sessions(path: Path) -> pd.DataFrame:
    return _read_parquet_cached(path)


class MetricsApp(BaseHTTPRequestHandler):
    metrics_path: Path = DEFAULT_METRICS
    decomposition_path: Path = DEFAULT_DECOMP
    segments_path: Path = DEFAULT_SEGMENTS
    sessions_path: Path = DEFAULT_SESSIONS

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path
        query = parse_qs(parsed.query)

        try:
            if path == "/api/timeline" or path == "/api/metrics":
                self._handle_timeline(query)
                return
            if path == "/api/day":
                self._handle_day(query)
                return
            if path == "/api/explain":
                self._handle_explain(query)
                return
            if path == "/api/anomalies":
                self._handle_anomalies(query)
                return
            if path == "/api/category":
                self._handle_category(query)
                return
            if path == "/api/segments":
                self._handle_segments(query)
                return
            if path == "/api/weak-spots":
                self._handle_weak_spots(query)
                return
            if path == "/api/examples":
                self._handle_examples(query)
                return
            if path == "/api/about":
                self._handle_about(query)
                return

            if path in ("/", "/index.html"):
                self._send_file(WEB_DIR / "index.html", "text/html; charset=utf-8")
                return
            served = self._maybe_serve_static(path)
            if served:
                return

            self.send_error(404)
        except FileNotFoundError as exc:
            self._send_json({
                "error": "missing artifact",
                "path": str(exc),
                "hint": "Run src/pipeline/run_daily_pipeline.py to materialise it.",
            }, status=503)
        except Exception as exc:
            self._send_json({"error": type(exc).__name__, "message": str(exc)}, status=500)

    def log_message(self, fmt: str, *args) -> None:
        return


    def _handle_timeline(self, query: dict[str, list[str]]) -> None:
        df = _read_metrics(self.metrics_path)
        if df.empty:
            self._send_json({
                "source": str(self.metrics_path.relative_to(ROOT)),
                "available_min": None,
                "available_max": None,
                "selected_start": None,
                "selected_end": None,
                "rows": [],
            })
            return
        start = query.get("start", [df["date"].min()])[0]
        end = query.get("end", [df["date"].max()])[0]
        filtered = df[(df["date"] >= start) & (df["date"] <= end)]
        payload = {
            "source": str(self.metrics_path.relative_to(ROOT)),
            "available_min": df["date"].min(),
            "available_max": df["date"].max(),
            "selected_start": start,
            "selected_end": end,
            "rows": filtered.to_dict(orient="records"),
        }
        self._send_json(payload)

    def _handle_day(self, query: dict[str, list[str]]) -> None:
        day = _required(query, "date")
        decomp = _read_decomposition(self.decomposition_path)
        sub = decomp[decomp["date"] == day]
        if sub.empty:
            self._send_json({"error": f"no decomposition for {day}"}, status=404)
            return
        groups = sub[sub["level"] == "group"].to_dict(orient="records")
        features = sub[sub["level"] == "feature"].to_dict(orient="records")
        categories = sub[sub["level"] == "category"].to_dict(orient="records")
        self._send_json({
            "date": day,
            "groups": groups,
            "features": features,
            "categories": categories,
        })

    def _handle_explain(self, query: dict[str, list[str]]) -> None:
        d_from = _required(query, "from")
        d_to = _required(query, "to")
        top_n = int(query.get("top_n", ["10"])[0])
        decomp = _read_decomposition(self.decomposition_path)
        result = explain_drop(decomp, d_from, d_to, top_n=top_n)
        self._send_json(result)

    def _handle_anomalies(self, query: dict[str, list[str]]) -> None:
        day = _required(query, "date")
        decomp = _read_decomposition(self.decomposition_path)
        anomalies = anomaly_summary(decomp, day)
        self._send_json({"date": day, "anomalies": anomalies, "n": len(anomalies)})

    def _handle_category(self, query: dict[str, list[str]]) -> None:
        day = _required(query, "date")
        cat = _required(query, "category")
        decomp = _read_decomposition(self.decomposition_path)
        record = category_drilldown(decomp, day, cat)
        self._send_json(record)

    def _handle_segments(self, query: dict[str, list[str]]) -> None:
        df = _read_segments(self.segments_path)
        segment_filter = query.get("segment", [None])[0]
        day = query.get("date", [None])[0]
        if "date" in df.columns:
            df = df[df["date"] == (day or "ALL")]
        if segment_filter:
            df = df[df["segment"] == segment_filter]
        grouped: dict[str, list[dict]] = {}
        for seg, sub in df.groupby("segment"):
            sub_sorted = sub.sort_values("n_sessions", ascending=False)
            grouped[str(seg)] = sub_sorted.to_dict(orient="records")
        self._send_json({"date": day or "ALL", "segments": grouped})

    def _handle_weak_spots(self, query: dict[str, list[str]]) -> None:
        df = _read_segments(self.segments_path)
        min_volume = int(query.get("min_volume", ["200"])[0])
        top_n = int(query.get("top", [query.get("top_n", ["20"])[0]])[0])
        min_lift = float(query.get("min_lift", ["-0.05"])[0])
        day = query.get("date", [None])[0]

        if day:
            baseline = self._baseline_period()
            result = segment_day_view(
                df, day, min_volume=min_volume, top_n=top_n,
                baseline_period=baseline,
            )
            norm_source = (
                result["norm_source"].iloc[0] if "norm_source" in result.columns and len(result)
                else ("baseline" if baseline else "period")
            )
            payload = {
                "mode": "day",
                "date": day,
                "min_volume": min_volume,
                "top_n": top_n,
                "norm": {
                    "source": norm_source,
                    "baseline_start": baseline[0] if baseline else None,
                    "baseline_end": baseline[1] if baseline else None,
                },
                "rows": result.to_dict(orient="records"),
            }
            if result.empty and "date" not in df.columns:
                payload["warning"] = (
                    "segment_breakdown has no per-day rows; rebuild via "
                    "run_daily_pipeline.py --segments-sessions …"
                )
            self._send_json(payload)
            return

        result = weak_spots(df, min_volume=min_volume, min_lift_threshold=min_lift, top_n=top_n)
        self._send_json({
            "mode": "period",
            "min_volume": min_volume,
            "min_lift_threshold": min_lift,
            "top_n": top_n,
            "rows": result.to_dict(orient="records"),
        })

    def _baseline_period(self) -> tuple[str, str] | None:
        try:
            df = _read_metrics(self.metrics_path)
            start = df.iloc[0].get("baseline_start")
            end = df.iloc[0].get("baseline_end")
            if start and end:
                return str(start), str(end)
        except Exception:
            pass
        return None

    def _handle_examples(self, query: dict[str, list[str]]) -> None:
        segment = _required(query, "segment")
        value = _required(query, "value")
        n = int(query.get("n", ["10"])[0])
        day = query.get("date", [None])[0]
        if not self.sessions_path.exists():
            self._send_json({
                "error": "sessions parquet not found",
                "path": str(self.sessions_path),
                "examples": [],
            }, status=503)
            return
        sessions = _read_sessions(self.sessions_path)
        examples = example_failures(sessions, segment, value, n=n, day=day)
        self._send_json({
            "segment": segment, "value": value, "date": day,
            "n": len(examples), "examples": examples,
        })

    def _handle_about(self, query: dict[str, list[str]]) -> None:
        def _stat(p: Path) -> dict:
            if not p.exists():
                return {"path": str(p), "exists": False}
            st = p.stat()
            return {
                "path": str(p.relative_to(ROOT) if ROOT in p.parents else p),
                "exists": True,
                "size_mb": round(st.st_size / 1024 / 1024, 2),
                "mtime": pd.Timestamp.fromtimestamp(st.st_mtime).isoformat(),
            }
        meta_payload = {
            "metrics": _stat(self.metrics_path),
            "decomposition": _stat(self.decomposition_path),
            "segments": _stat(self.segments_path),
            "sessions": _stat(self.sessions_path),
        }
        try:
            df = _read_metrics(self.metrics_path)
            meta_payload["baseline_start"] = df.iloc[0].get("baseline_start")
            meta_payload["baseline_end"] = df.iloc[0].get("baseline_end")
            meta_payload["n_days"] = int(len(df))
        except Exception:
            pass
        self._send_json(meta_payload)


    _STATIC_TYPES = {
        ".js": "application/javascript; charset=utf-8",
        ".mjs": "application/javascript; charset=utf-8",
        ".css": "text/css; charset=utf-8",
        ".svg": "image/svg+xml; charset=utf-8",
        ".html": "text/html; charset=utf-8",
        ".json": "application/json; charset=utf-8",
        ".woff": "font/woff",
        ".woff2": "font/woff2",
        ".ttf": "font/ttf",
        ".otf": "font/otf",
    }

    def _maybe_serve_static(self, url_path: str) -> bool:
        rel = url_path.lstrip("/")
        if not rel:
            return False
        target = (WEB_DIR / rel).resolve()
        try:
            target.relative_to(WEB_DIR.resolve())
        except ValueError:
            return False
        if not target.is_file():
            return False
        suffix = target.suffix.lower()
        ctype = self._STATIC_TYPES.get(suffix)
        if ctype is None:
            return False
        self._send_file(target, ctype)
        return True

    def _send_file(self, path: Path, content_type: str) -> None:
        data = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _send_json(self, payload: dict, status: int = 200) -> None:
        sanitized = _sanitize_nan(payload)
        data = json.dumps(
            sanitized, ensure_ascii=False, default=_json_default, allow_nan=False
        ).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def _required(query: dict[str, list[str]], key: str) -> str:
    val = query.get(key)
    if not val:
        raise ValueError(f"missing required query param: {key!r}")
    return val[0]


def main() -> None:
    parser = argparse.ArgumentParser(description="Run local Health Score RCA web UI.")
    parser.add_argument("--metrics", default=str(DEFAULT_METRICS), help="Path to daily_metrics parquet")
    parser.add_argument("--decomposition", default=str(DEFAULT_DECOMP), help="Path to decomposition parquet")
    parser.add_argument("--segments", default=str(DEFAULT_SEGMENTS), help="Path to segment_breakdown parquet")
    parser.add_argument("--sessions", default=str(DEFAULT_SESSIONS),
                        help="Path to intent_sessions_with_query_features parquet (for examples)")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()

    MetricsApp.metrics_path = Path(args.metrics).resolve()
    MetricsApp.decomposition_path = Path(args.decomposition).resolve()
    MetricsApp.segments_path = Path(args.segments).resolve()
    MetricsApp.sessions_path = Path(args.sessions).resolve()

    server = ThreadingHTTPServer((args.host, args.port), MetricsApp)
    print(f"Health Score RCA UI on http://{args.host}:{args.port}", flush=True)
    print(f"  metrics       = {MetricsApp.metrics_path}", flush=True)
    print(f"  decomposition = {MetricsApp.decomposition_path}", flush=True)
    print(f"  segments      = {MetricsApp.segments_path}", flush=True)
    print(f"  sessions      = {MetricsApp.sessions_path}", flush=True)

    if MetricsApp.sessions_path.exists():
        import time
        t0 = time.monotonic()
        print(f"  warming sessions cache (~370MB)…", end="", flush=True)
        _read_sessions(MetricsApp.sessions_path)
        print(f" done in {time.monotonic() - t0:.1f}s", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
