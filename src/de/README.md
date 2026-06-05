# src/de — Data Engineering research

Не production. Здесь research-ноутбуки DE-команды.

- `bot_filter.ipynb` — детектор ботов: feature-engineering + grid search.
- `bot_scorer_grid_results.csv` — таблица результатов grid search для bot scorer.
- `daily_summary_check.ipynb` — sanity-check day_summary на новых данных.

Production-сборка sessions / day_summary живёт в `src/pipeline/`. Эти ноутбуки — кандидаты на промоцию в pipeline после валидации.
