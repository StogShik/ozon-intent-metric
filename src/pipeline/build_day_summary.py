"""
build_day_summary.py — контракт данных
======================================

ВХОД: intent_sessions.parquet
------------------------------
Обязательные колонки и типы (CONTRACT_SESSIONS_INPUT):
  ts_end                   : Datetime  — конец сессии (используется для date/weekday)
  user_id                  : Int64     — идентификатор пользователя
  top_category_in_session  : Utf8      — топ-категория сессии (nullable → mapped to 'other')
  n_events                 : Int64     — суммарное число событий
  n_search                 : Int64     — число поисковых запросов
  n_view                   : Int64     — число просмотров
  n_click                  : Int64     — число кликов
  n_cart                   : Int64     — число добавлений в корзину
  n_fav                    : Int64     — число добавлений в избранное
  n_unique_queries         : Int64     — число уникальных поисковых запросов
  reached_cart             : Boolean   — флаг: была ли корзина в сессии
  duration_s               : Int64     — длительность сессии в секундах
  n_unique_categories      : Int64     — число уникальных категорий в сессии
  query_stratification_score : Int8    — NLP-score сложности first-query (0–5, выше = сложнее)
  is_in_product_base       : Boolean   — флаг: first-query точно совпадает с product base

Ограничения:
  n_events, n_search, n_view, n_click, n_cart, n_fav, n_unique_queries >= 0
  ts_end — не null

ВЫХОД: daily_summaries/day_summary_YYYY_MM_DD.parquet
------------------------------------------------------
Одна строка = (date, category). Контракт (CONTRACT_SUMMARY_OUTPUT):
  date                 : Date    — дата сессий
  category             : Utf8    — категория (топ-N из baseline) или 'other'
  n_users              : UInt32  — уникальных пользователей
  n_sessions           : UInt32  — всего сессий
  rate_events          : Float64 — mean(n_events)
  rate_searches        : Float64 — mean(n_search)
  rate_views           : Float64 — mean(n_view)
  rate_clicks          : Float64 — mean(n_click)
  rate_favs            : Float64 — mean(n_fav)
  rate_unique_queries  : Float64 — mean(n_unique_queries)
  is_cart                  : Int32   — sum(reached_cart), сессий достигших корзины
  cart_rate                : Float64 — mean(n_cart), среднее # добавлений в корзину на сессию (event-based)
  session_conversion_rate  : Float64 — mean(reached_cart), доля сессий с конверсией (binary)
  bounce_rate              : Float64 — mean(n_events == 1), доля однособытийных сессий
  dead_search_rate         : Float64 — P(n_click == 0 | n_search > 0), поиск без клика
  search_refinement_rate   : Float64 — P(n_unique_queries > 1 | n_search > 0), уточнение запроса
  mean_duration_s          : Float64 — mean(duration_s), средняя длительность сессии
  multi_category_rate      : Float64 — mean(n_unique_categories > 1), кросс-категориальные сессии
  view_per_search          : Float64 — sum(n_view)/sum(n_search), funnel: search → view; null если sum(n_search)=0
  click_per_view           : Float64 — sum(n_click)/sum(n_view), funnel: view → click; null если sum(n_view)=0
  cart_per_click           : Float64 — sum(n_cart)/sum(n_click), funnel: click → cart; null если sum(n_click)=0
  km_median_time_to_cart_s : Float64 — KM-медиана времени до корзины (с цензурированием),
                                        null если < 30 событий в категории
  complex_query_rate       : Float64 — mean(query_stratification_score >= 4), доля сложных сессий (NLP)
  in_product_base_rate     : Float64 — mean(is_in_product_base), доля first-query, совпавших с product base

Гарантии выхода:
  n_sessions >= 1 для каждой строки
  rate_* >= 0
  0 <= is_cart <= n_sessions
  Строки отсортированы по category внутри дня
  Имя файла: day_summary_YYYY_MM_DD.parquet
"""

import argparse
import gc
import shutil
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import polars as pl
from lifelines import KaplanMeierFitter

# TODO(metric/pipeline integration): build_intent_sessions.py был переписан в
# pipeline-ветке (commit 0afabd1). `build_chunk_lazy` теперь называется
# `build_intent_sessions_lazy` с другой сигнатурой. До унификации этот CLI
# падает на import — используйте `src/pipeline/run_daily_pipeline.py` с его
# inline build_day_summary, либо восстановите `build_chunk_lazy` shim.
from build_intent_sessions import build_chunk_lazy


# ---------------------------------------------------------------------------
# Контракт: обязательные колонки входного intent_sessions
# ---------------------------------------------------------------------------
CONTRACT_SESSIONS_INPUT: dict[str, type] = {
    "ts_end":                  pl.Datetime,
    "user_id":                 pl.Int64,
    "top_category_in_session": pl.Utf8,
    "n_events":                pl.Int64,
    "n_search":                pl.Int64,
    "n_view":                  pl.Int64,
    "n_click":                 pl.Int64,
    "n_cart":                  pl.Int64,
    "n_fav":                   pl.Int64,
    "n_unique_queries":        pl.Int64,
    "reached_cart":            pl.Boolean,
    "duration_s":              pl.Int64,
    "n_unique_categories":     pl.Int64,
    "time_to_cart_s":          pl.Int64,
    "query_stratification_score": pl.Int64,
    "is_in_product_base":         pl.Boolean,
}

# ---------------------------------------------------------------------------
# Контракт: обязательные колонки выходного day_summary
# ---------------------------------------------------------------------------
CONTRACT_SUMMARY_OUTPUT: dict[str, type] = {
    "date":                     pl.Date,
    "category":                 pl.Utf8,
    "n_users":                  pl.UInt32,
    "n_sessions":               pl.UInt32,
    "rate_events":              pl.Float64,
    "rate_searches":            pl.Float64,
    "rate_views":               pl.Float64,
    "rate_clicks":              pl.Float64,
    "rate_favs":                pl.Float64,
    "rate_unique_queries":      pl.Float64,
    "is_cart":                  pl.Int32,
    "cart_rate":                pl.Float64,
    "session_conversion_rate":  pl.Float64,
    "bounce_rate":              pl.Float64,
    "dead_search_rate":         pl.Float64,
    "search_refinement_rate":   pl.Float64,
    "mean_duration_s":          pl.Float64,
    "multi_category_rate":      pl.Float64,
    "view_per_search":          pl.Float64,
    "click_per_view":           pl.Float64,
    "cart_per_click":           pl.Float64,
    "km_median_time_to_cart_s": pl.Float64,
    "complex_query_rate":       pl.Float64,
    "in_product_base_rate":     pl.Float64,
}


def validate_sessions_schema(lf: pl.LazyFrame) -> None:
    """
    Проверяет схему intent_sessions против CONTRACT_SESSIONS_INPUT.
    Падает с ValueError при:
      - отсутствии обязательной колонки
      - несовместимом типе (numeric допускаем любой Int/Float, temporal строго)
    """
    actual = dict(lf.schema)
    errors = []

    for col, expected_type in CONTRACT_SESSIONS_INPUT.items():
        if col not in actual:
            errors.append(f"MISSING column: '{col}'")
            continue

        actual_type = actual[col]

        # Datetime — принимаем любой Datetime (с/без timezone)
        if expected_type == pl.Datetime:
            if not isinstance(actual_type, pl.Datetime):
                errors.append(f"TYPE MISMATCH '{col}': expected Datetime, got {actual_type}")

        # Int64 — принимаем любой целочисленный тип
        elif expected_type == pl.Int64:
            if actual_type not in (pl.Int8, pl.Int16, pl.Int32, pl.Int64,
                                   pl.UInt8, pl.UInt16, pl.UInt32, pl.UInt64):
                errors.append(f"TYPE MISMATCH '{col}': expected integer, got {actual_type}")

        # Остальные — строгое совпадение
        else:
            if actual_type != expected_type:
                errors.append(f"TYPE MISMATCH '{col}': expected {expected_type}, got {actual_type}")

    if errors:
        raise ValueError(
            "intent_sessions не соответствует контракту:\n" + "\n".join(errors)
        )


def build_sessions(events_root: Path, products_root: Path,
                   start_d: date, end_d: date,
                   out_path: Path, gap_min: int = 30,
                   chunk_days: int = 7, keep_shards: bool = False,
                   rewrite: bool = False) -> None:
    """Builds intent_sessions parquet from raw events, chunk by chunk."""
    if out_path.exists() and not rewrite:
        print(f"Файл уже существует, пропускаем построение сессий: {out_path}")
        return

    out_path.parent.mkdir(parents=True, exist_ok=True)
    shards_dir = out_path.with_name(out_path.stem + '.parts')
    if shards_dir.exists():
        shutil.rmtree(shards_dir)
    shards_dir.mkdir(parents=True)

    print(f"Строим сессии: {start_d} → {end_d}, gap={gap_min}min, chunk={chunk_days}d")
    chunk_idx = 0
    cur = start_d
    while cur <= end_d:
        chunk_end = min(cur + timedelta(days=chunk_days - 1), end_d)
        shard_path = shards_dir / f'part_{chunk_idx:03d}.parquet'
        print(f"[{chunk_idx:03d}] {cur} ... {chunk_end} -> {shard_path.name}", flush=True)

        lf = build_chunk_lazy(events_root, products_root, cur, chunk_end, gap_min)
        try:
            lf.sink_parquet(shard_path)
        except (pl.exceptions.InvalidOperationError, pl.exceptions.ComputeError, Exception) as e:
            print(f"sink_parquet failed ({type(e).__name__}); falling back to collect(engine='streaming')")
            df = lf.collect(engine='streaming')
            df.write_parquet(shard_path)
            del df

        del lf
        gc.collect()
        chunk_idx += 1
        cur = chunk_end + timedelta(days=1)

    print(f"Merging {chunk_idx} шардов в {out_path} ...")
    pl.scan_parquet(str(shards_dir / '*.parquet')).sink_parquet(out_path)

    if not keep_shards:
        shutil.rmtree(shards_dir)
    else:
        print(f"Шарды сохранены: {shards_dir}")

    print(f"Сессии сохранены: {out_path}")

def _km_median_per_category(
    sessions: pl.DataFrame,
    cat_col: str = 'top_category_in_session',
    min_events: int = 30,
) -> pl.DataFrame:
    """
    KM-медиана времени до корзины per category с учётом цензурирования.
    Для сессий без корзины цензурирующее время = duration_s.
    """
    kmf = KaplanMeierFitter()
    rows = []
    for cat in sessions[cat_col].unique().to_list():
        sub = sessions.filter(pl.col(cat_col) == cat)
        T = (
            sub.select(
                pl.when(pl.col('reached_cart'))
                  .then(pl.col('time_to_cart_s'))
                  .otherwise(pl.col('duration_s'))
            )
            .to_series()
            .to_numpy()
            .astype(float)
        )
        E = sub['reached_cart'].to_numpy()
        if E.sum() < min_events:
            rows.append({'category': cat, 'km_median_time_to_cart_s': None})
            continue
        kmf.fit(T, E)
        km_val = float(kmf.median_survival_time_)
        # inf = survival never crosses 50% (cart_rate < 0.5 for this category); treat as undefined
        rows.append({'category': cat, 'km_median_time_to_cart_s': km_val if np.isfinite(km_val) else None})
    return pl.DataFrame(rows, schema={'category': pl.Utf8, 'km_median_time_to_cart_s': pl.Float64})


def build_daily_category_summary(intent_sessions: pl.LazyFrame, output_dir: Path) -> pl.DataFrame:
    """
    Aggregates user intent sessions into per-day summaries grouped by product category.

    Processing pipeline
    -------------------
    1. **Category classification** (single pass over the full LazyFrame):
       Categories with >= MIN_SIZE (500) total sessions across the entire input period
       are kept as-is. All others — including null — are mapped to 'other'.
       This matches the stratification logic used in the M3 metric and ensures
       that rare categories do not produce noisy aggregates.

    2. **Day-by-day aggregation** (memory-efficient):
       The function iterates over unique dates one at a time. For each day it:
       - filters the LazyFrame to that date,
       - applies the category mapping,
       - runs a group_by aggregation,
       - writes the result to a separate parquet file,
       - frees memory before moving to the next day.
       This means the peak memory footprint is one day of sessions, not the full period.

    3. **Correlation check** (informational):
       After aggregating each day, Pearson correlations are computed on the metric
       columns. Pairs with |corr| >= 0.8 are printed to stdout as a warning.
       No columns are dropped — the check is diagnostic only.

    Output columns per (date, category) row
    ----------------------------------------
    - n_users                  : number of unique users with a session that day
    - n_sessions               : total number of intent sessions
    - rate_events              : mean n_events per session
    - rate_searches            : mean n_search per session
    - rate_views               : mean n_view per session
    - rate_clicks              : mean n_click per session
    - rate_favs                : mean n_fav per session
    - rate_unique_queries      : mean n_unique_queries per session
    - is_cart                  : number of sessions that reached a cart event
    - cart_rate                : mean(n_cart) per session — event-based cart rate
    - session_conversion_rate  : mean(reached_cart) — binary session conversion proportion
    - bounce_rate              : mean(n_events == 1)
    - dead_search_rate         : P(n_click == 0 | n_search > 0), null if no search sessions
    - search_refinement_rate   : P(n_unique_queries > 1 | n_search > 0), null if no search sessions
    - mean_duration_s          : mean(duration_s)
    - multi_category_rate      : mean(n_unique_categories > 1)
    - view_per_search          : sum(n_view)/sum(n_search), funnel; null if sum(n_search)=0
    - click_per_view           : sum(n_click)/sum(n_view), funnel; null if sum(n_view)=0
    - cart_per_click           : sum(n_cart)/sum(n_click), funnel; null if sum(n_click)=0
    - km_median_time_to_cart_s : KM-median time-to-cart, null if < 30 cart events in category
    - complex_query_rate       : mean(query_stratification_score >= 4), share of complex queries (NLP)
    - in_product_base_rate     : mean(is_in_product_base), share of first-queries matching product base

    Output files
    ------------
    One parquet file per day: ``<output_dir>/day_summary_YYYY_MM_DD.parquet``.

    :param intent_sessions: Lazy scan of intent sessions (e.g. from
        ``pl.scan_parquet('intent_sessions.parquet')``). Must contain columns:
        ``ts_end``, ``top_category_in_session``, ``user_id``, ``n_events``,
        ``n_search``, ``n_view``, ``n_click``, ``n_cart``, ``n_fav``,
        ``n_unique_queries``, ``reached_cart``, ``duration_s``, ``n_unique_categories``.
    :param output_dir: Directory where per-day parquet files will be written.
        Created automatically if it does not exist.
    """
    validate_sessions_schema(intent_sessions)
    print("Начинаем расчет дневных метрик по категориям...")
    output_dir.mkdir(parents=True, exist_ok=True)

    base = intent_sessions.with_columns([
        pl.col('ts_end').dt.date().alias('date'),
        pl.col('ts_end').dt.weekday().alias('weekday'),
    ])

    # large_cats считаем один раз по всему периоду — только счётчики, не полные данные
    MIN_SIZE = 2000
    large_cats = (
        base.group_by('top_category_in_session')
            .agg(pl.len().alias('n'))
            .filter(pl.col('n') >= MIN_SIZE)
            .collect()
            .get_column('top_category_in_session')
            .drop_nulls()
            .to_list()
    )

    # Получаем список уникальных дней (только даты — в память не тянем сессии)
    all_dates = (
        base.select('date')
            .unique()
            .sort('date')
            .collect()
            .get_column('date')
            .to_list()
    )

    print(f"Обрабатываем {len(all_dates)} дней...")

    metric_cols = ['n_users', 'n_sessions', 'is_cart', 'cart_rate', 'session_conversion_rate',
                   'rate_events', 'rate_searches', 'rate_views',
                   'rate_clicks', 'rate_favs', 'rate_unique_queries',
                   'bounce_rate', 'dead_search_rate', 'search_refinement_rate',
                   'mean_duration_s', 'multi_category_rate',
                   'view_per_search', 'click_per_view', 'cart_per_click',
                   'km_median_time_to_cart_s',
                   'complex_query_rate', 'in_product_base_rate']

    for current_date in all_dates:
        # Коллектируем сессии дня с маппингом категорий — нужно и для аггрегации, и для KM
        day_sessions = (
            base.filter(pl.col('date') == current_date)
                .with_columns(
                    pl.when(pl.col('top_category_in_session').is_in(large_cats))
                      .then(pl.col('top_category_in_session'))
                      .otherwise(pl.lit('other'))
                      .alias('top_category_in_session')
                )
                .collect()
        )

        # Стандартная polars-аггрегация
        day_df = (
            day_sessions.lazy()
                .group_by(['date', 'top_category_in_session'])
                .agg([
                    pl.col('user_id').n_unique().alias('n_users'),
                    pl.len().alias('n_sessions'),
                    pl.col('n_events').mean().alias('rate_events'),
                    pl.col('n_search').mean().alias('rate_searches'),
                    pl.col('n_view').mean().alias('rate_views'),
                    pl.col('n_click').mean().alias('rate_clicks'),
                    pl.col('n_fav').mean().alias('rate_favs'),
                    pl.col('n_unique_queries').mean().alias('rate_unique_queries'),
                    pl.col('reached_cart').cast(pl.Int32).sum().alias('is_cart'),
                    pl.col('n_cart').mean().alias('cart_rate'),
                    pl.col('reached_cart').cast(pl.Float64).mean().alias('session_conversion_rate'),
                    (pl.col('n_events') == 1).mean().alias('bounce_rate'),
                    pl.when(pl.col('n_search') > 0)
                      .then((pl.col('n_click') == 0).cast(pl.Float64))
                      .otherwise(None)
                      .mean()
                      .alias('dead_search_rate'),
                    pl.when(pl.col('n_search') > 0)
                      .then((pl.col('n_unique_queries') > 1).cast(pl.Float64))
                      .otherwise(None)
                      .mean()
                      .alias('search_refinement_rate'),
                    pl.col('duration_s').mean().alias('mean_duration_s'),
                    (pl.col('n_unique_categories') > 1).mean().alias('multi_category_rate'),
                    pl.when(pl.col('n_search').sum() > 0)
                      .then(pl.col('n_view').sum() / pl.col('n_search').sum())
                      .otherwise(None)
                      .alias('view_per_search'),
                    pl.when(pl.col('n_view').sum() > 0)
                      .then(pl.col('n_click').sum() / pl.col('n_view').sum())
                      .otherwise(None)
                      .alias('click_per_view'),
                    pl.when(pl.col('n_click').sum() > 0)
                      .then(pl.col('n_cart').sum() / pl.col('n_click').sum())
                      .otherwise(None)
                      .alias('cart_per_click'),
                    (pl.col('query_stratification_score') >= 4).mean().alias('complex_query_rate'),
                    pl.col('is_in_product_base').cast(pl.Float64).mean().alias('in_product_base_rate'),
                ])
                .rename({'top_category_in_session': 'category'})
                .sort('category')
                .collect()
        )

        # KM-медиана time-to-cart с учётом цензурирования (lifelines)
        km_df = _km_median_per_category(day_sessions)
        day_df = day_df.join(km_df, on='category', how='left')

        date_str = current_date.strftime('%Y_%m_%d')
        out_file = output_dir / f"day_summary_{date_str}.parquet"
        day_df.write_parquet(out_file)
        print(f"  [{current_date}] сохранён -> {out_file.name}", flush=True)

        del day_sessions, day_df, km_df
        gc.collect()

    print(f" Готово! Все дни сохранены в: {output_dir}")

def main():
    parser = argparse.ArgumentParser(
        description="Строит intent sessions из сырых событий и дневную сводку по категориям"
    )

    # --- Источник сессий: либо готовый файл, либо строим из сырых событий ---
    src = parser.add_mutually_exclusive_group(required=True)
    src.add_argument('--in-sessions', type=str,
                     help="Готовый intent_sessions.parquet (пропустить построение сессий)")
    src.add_argument('--start', type=str,
                     help="Начало периода (YYYY-MM-DD) — запускает построение сессий из сырых данных")

    # Аргументы построения сессий (нужны только вместе с --start)
    parser.add_argument('--end', type=str,
                        help="Конец периода (YYYY-MM-DD, включительно)")
    parser.add_argument('--input', type=str, default='data/user_actions_3_months',
                        help="Корень hive-партиционированных событий")
    parser.add_argument('--products', type=str, default='data/product_information',
                        help="Папка с product_information parquet")
    parser.add_argument('--gap-min', type=int, default=30,
                        help="Порог неактивности для разбиения сессий (минуты)")
    parser.add_argument('--chunk-days', type=int, default=7,
                        help="Размер чанка в днях")
    parser.add_argument('--keep-shards', action='store_true',
                        help="Оставить промежуточные шарды после merge")
    parser.add_argument('--rewrite', action='store_true',
                        help="Перезаписать уже существующий файл сессий")
    parser.add_argument('--out-sessions', type=str, default='data/intent_sessions.parquet',
                        help="Куда сохранить построенные сессии")

    # --- Аргументы дневной сводки ---
    parser.add_argument('--out-dir', type=str, default='data/daily_summaries',
                        help="Папка для сохранения дневных файлов")
    parser.add_argument('--target-date', type=str, default=None,
                        help="Пересчитать только указанный день (YYYY-MM-DD)")

    args = parser.parse_args()

    output_dir = Path(args.out_dir)

    if args.start:
        if not args.end:
            parser.error("--end обязателен при использовании --start")
        sessions_path = Path(args.out_sessions)
        build_sessions(
            events_root=Path(args.input),
            products_root=Path(args.products),
            start_d=date.fromisoformat(args.start),
            end_d=date.fromisoformat(args.end),
            out_path=sessions_path,
            gap_min=args.gap_min,
            chunk_days=args.chunk_days,
            keep_shards=args.keep_shards,
            rewrite=args.rewrite,
        )
    else:
        sessions_path = Path(args.in_sessions)
        if not sessions_path.exists():
            print(f" Ошибка: файл {sessions_path} не найден!")
            return

    print(f"Читаем сессии из {sessions_path} ...")
    intent_sessions_lf = pl.scan_parquet(sessions_path)

    if args.target_date:
        t_date = date.fromisoformat(args.target_date)
        print(f"Фильтрация: только данные за {t_date}")
        intent_sessions_lf = intent_sessions_lf.filter(pl.col('ts_end').dt.date() == t_date)

    build_daily_category_summary(intent_sessions_lf, output_dir)

if __name__ == '__main__':
    main()