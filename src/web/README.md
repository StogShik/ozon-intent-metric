# src/web — Web UI

Дашборд аналитика: Health Score timeline · drill в день · Δ explain ·
weak spots (за период и за день) · реальные примеры провалов.

- `server.py` — HTTP-сервер (Python stdlib), JSON API поверх parquet-артефактов
  пайплайна; query-логика — `src/metric/rca.py`.
- `index.html`, `app.js`, `styles.css`, `views/`, `components/`, `lib/` —
  frontend на ES-modules, без build step.

## Запуск

```bash
python src/web/server.py --metrics data/daily_metrics_full.parquet --port 8760
# открыть http://127.0.0.1:8760
```

## Артефакты (default-пути в `data/`, флаги для переопределения)

| Артефакт | Флаг | Питает |
|---|---|---|
| `daily_metrics_full.parquet` | `--metrics` | Overview timeline, KPIs |
| `decomposition_full.parquet` | `--decomposition` | Day detail, Δ explain, anomalies |
| `segment_breakdown.parquet` | `--segments` | Weak spots (период + per-day) |
| `intent_sessions_with_query_features.parquet` | `--sessions` | Examples (провальные сессии) |

Без segment_breakdown / sessions дашборд работает частично (timeline и day
drill живы, weak spots / examples — нет).

## API

`/api/timeline`, `/api/day?date=`, `/api/explain?from=&to=`,
`/api/anomalies?date=`, `/api/category?date=&category=`,
`/api/segments[?date=]`, `/api/weak-spots[?date=]` (period mode: failure
volume; day mode: extra failures vs норма сегмента за baseline-окно Health
Score, fallback — весь период), `/api/examples?segment=&value=[&date=]`,
`/api/about`.
