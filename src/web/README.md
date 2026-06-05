# src/web — Web UI

Локальный сайт для просмотра `daily_metrics.parquet`.

- `server.py` — HTTP-сервер (Python stdlib).
- `index.html`, `app.js`, `styles.css` — frontend (no build step).

## Запуск

```bash
python src/web/server.py --metrics data/daily_metrics_full.parquet --port 8760
# открыть http://127.0.0.1:8760
```

Зависит только от `daily_metrics_*.parquet` от `src/pipeline/run_daily_pipeline.py`.
