import argparse
import polars as pl
import numpy as np
from pathlib import Path

def main():
    parser = argparse.ArgumentParser(description="Сборка целевых сессий пользователей")

    parser.add_argument('--start', type=str, required=True, help="Начало периода (YYYY-MM-DD)")
    parser.add_argument('--end', type=str, required=True, help="Конец периода (YYYY-MM-DD)")
    parser.add_argument('--out', type=str, required=True, help="Путь для сохранения результата (.parquet)")
    parser.add_argument('--gap-min', type=int, default=30, help="Таймаут сессии в минутах")

    args = parser.parse_args()

    print(f"Запуск сборки сессий с {args.start} по {args.end}...")
    print(f"Используем таймаут сессии: {args.gap_min} минут")

    input_path = Path() # ВНЕСТИ ПУТЬ

    events = (
        pl.scan_parquet(str(input_path / '**/*.parquet'), hive_partitioning=True)
            .filter((pl.col('date') >= args.start) & (pl.col('date') <= args.end))
    )

    gap_thresh = args.gap_min * 60

    ev = (
        events.sort(['user_id', 'timestamp'])
            .with_columns(
                (pl.col('timestamp').diff().over('user_id').dt.total_seconds()
                    .fill_null(gap_thresh + 1) > gap_thresh).alias('new_session')
            )
            .with_columns(pl.col('new_session').cast(pl.Int32).cum_sum().over('user_id').alias('session_idx'))
    )

    ev = ev.with_columns(
        pl.when(pl.col('action_type') == 'search')
        .then(pl.col('search_query'))
        .otherwise(None)
        .alias('_q')
    ).with_columns(
        pl.col('_q').forward_fill().over(['user_id', 'session_idx']).alias('active_query')
    ).drop('_q')

    sessions = (
    ev.group_by(['user_id', 'session_idx'])
      .agg([
          pl.col('timestamp').min().alias('ts_start'),
          pl.col('timestamp').max().alias('ts_end'),
          pl.len().alias('n_events'),
          (pl.col('action_type') == 'search').sum().alias('n_search'),
          (pl.col('action_type') == 'view').sum().alias('n_view'),
          (pl.col('action_type') == 'click').sum().alias('n_click'),
          (pl.col('action_type') == 'to_cart').sum().alias('n_cart'),
          (pl.col('action_type') == 'favorite').sum().alias('n_fav'),
          pl.col('search_query').filter(pl.col('action_type') == 'search').n_unique().alias('n_unique_queries'),
          pl.col('search_query').filter(pl.col('action_type') == 'search').drop_nulls().first().alias('first_query'),
          pl.col('widget_name').filter(pl.col('action_type') == 'search').drop_nulls().first().alias('first_widget'),
      ])
      .with_columns([
          (pl.col('ts_end') - pl.col('ts_start')).dt.total_seconds().alias('duration_s'),
          (pl.col('n_cart') > 0).alias('reached_cart'),
      ])
    )

    intent_sessions = sessions.filter(pl.col('n_search') > 0)

    cart_ts = (
        ev.filter(pl.col('action_type') == 'to_cart')
        .group_by(['user_id', 'session_idx'])
        .agg(pl.col('timestamp').min().alias('ts_first_cart'))
    )
    intent_sessions = intent_sessions.join(cart_ts, on=['user_id','session_idx'], how='left').with_columns(
        (pl.col('ts_first_cart') - pl.col('ts_start')).dt.total_seconds().alias('time_to_cart_s')
    )

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    intent_sessions.collect().write_parquet(out_path)
    print(f"Готово! Результат сохранен в {args.out}")

if __name__ == '__main__':
    main()