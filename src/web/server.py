from __future__ import annotations

import argparse
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pandas as pd


ROOT = Path(__file__).resolve().parents[2]
WEB_DIR = Path(__file__).resolve().parent
DEFAULT_METRICS = ROOT / "data" / "daily_metrics_week_test.parquet"


def _json_default(value):
    if pd.isna(value):
        return None
    if hasattr(value, "item"):
        return value.item()
    return str(value)


class MetricsApp(BaseHTTPRequestHandler):
    metrics_path: Path = DEFAULT_METRICS

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/api/metrics":
            self._handle_metrics(parse_qs(parsed.query))
            return
        if parsed.path in ("/", "/index.html"):
            self._send_file(WEB_DIR / "index.html", "text/html; charset=utf-8")
            return
        if parsed.path == "/styles.css":
            self._send_file(WEB_DIR / "styles.css", "text/css; charset=utf-8")
            return
        if parsed.path == "/app.js":
            self._send_file(WEB_DIR / "app.js", "application/javascript; charset=utf-8")
            return
        self.send_error(404)

    def log_message(self, fmt: str, *args) -> None:
        return

    def _load_metrics(self) -> pd.DataFrame:
        if not self.metrics_path.exists():
            raise FileNotFoundError(f"metrics parquet not found: {self.metrics_path}")
        df = pd.read_parquet(self.metrics_path)
        df["date"] = pd.to_datetime(df["date"]).dt.strftime("%Y-%m-%d")
        return df.sort_values("date")

    def _handle_metrics(self, query: dict[str, list[str]]) -> None:
        try:
            df = self._load_metrics()
            start = query.get("start", [df["date"].min()])[0]
            end = query.get("end", [df["date"].max()])[0]
            filtered = df[(df["date"] >= start) & (df["date"] <= end)].copy()

            columns = [
                "date",
                "health_score",
                "stratified_health_score",
                "traffic_drift_signal",
                "mean_searches_to_cart",
                "mean_unique_queries_to_cart",
                "mean_time_to_cart_s",
                "n_sessions",
                "converted_sessions",
                "group_quality",
                "group_engagement",
                "group_discovery",
            ]
            available_columns = [col for col in columns if col in filtered.columns]
            payload = {
                "source": str(self.metrics_path.relative_to(ROOT)),
                "available_min": df["date"].min(),
                "available_max": df["date"].max(),
                "selected_start": start,
                "selected_end": end,
                "rows": filtered[available_columns].to_dict(orient="records"),
            }
            self._send_json(payload)
        except Exception as exc:
            self._send_json({"error": str(exc)}, status=500)

    def _send_file(self, path: Path, content_type: str) -> None:
        data = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _send_json(self, payload: dict, status: int = 200) -> None:
        data = json.dumps(payload, ensure_ascii=False, default=_json_default).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def main() -> None:
    parser = argparse.ArgumentParser(description="Run local daily metrics web UI.")
    parser.add_argument("--metrics", default=str(DEFAULT_METRICS), help="Path to daily_metrics parquet")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()

    MetricsApp.metrics_path = Path(args.metrics).resolve()
    server = ThreadingHTTPServer((args.host, args.port), MetricsApp)
    print(f"Serving {MetricsApp.metrics_path} at http://{args.host}:{args.port}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
