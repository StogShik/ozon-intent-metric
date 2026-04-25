import argparse
import gc
import shutil
from datetime import date, timedelta
from pathlib import Path

import polars as pl


def build_chunk_lazy(events_root: Path, products_root: Path,
                     start_d: date, end_d: date, gap_min: int) -> pl.LazyFrame:
    """Build a lazy plan for one chunk of intent sessions.

    Reads events in [start_d, end_d], splits them into sessions by a
    `gap_min`-minute inactivity gap, joins product info, and aggregates
    one row per (user_id, session_idx). Sessions without any `search`
    are dropped.

    :param events_root: Root of hive-partitioned event parquets
        (`date=*/action_type=*/*.parquet`).
    :param products_root: Folder with `product_information` parquet
        files (must contain `item_id`, `brand`, `category_name`).
    :param start_d: Inclusive start date of the chunk.
    :param end_d: Inclusive end date of the chunk.
    :param gap_min: Inactivity threshold in minutes that separates
        sessions of the same user.
    :return: Unmaterialized `pl.LazyFrame` matching the
        `intent_sessions` schema in `docs/schema.md`. The caller runs
        `.collect()` or `.sink_parquet(...)` to execute the plan.
    """
    events = (
        pl.scan_parquet(str(events_root / '**/*.parquet'), hive_partitioning=True)
            .filter((pl.col('date') >= start_d) & (pl.col('date') <= end_d))
    )

    products = (
        pl.scan_parquet(str(products_root / '*.parquet'))
            .select(['item_id', 'brand', 'category_name'])
    )

    gap_thresh = gap_min * 60

    ev = (
        events.sort(['user_id', 'timestamp'])
            .with_columns(
                (pl.col('timestamp').diff().over('user_id').dt.total_seconds()
                    .fill_null(gap_thresh + 1) > gap_thresh).alias('new_session')
            )
            .with_columns(pl.col('new_session').cast(pl.Int32).cum_sum().over('user_id').alias('session_idx'))
            .join(products, on='item_id', how='left')
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
          pl.col('category_name').drop_nulls().n_unique().alias('n_unique_categories'),
          pl.col('category_name').drop_nulls().mode().first().alias('top_category_in_session'),
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

    return intent_sessions


def main():
    parser = argparse.ArgumentParser(description="Build user intent sessions")

    parser.add_argument('--start', type=str, required=True, help="Period start (YYYY-MM-DD, inclusive)")
    parser.add_argument('--end', type=str, required=True, help="Period end (YYYY-MM-DD, inclusive)")
    parser.add_argument('--out', type=str, required=True, help="Output parquet path")
    parser.add_argument('--gap-min', type=int, default=30, help="Session inactivity threshold in minutes")
    parser.add_argument('--input', type=str, default='data/user_actions_3_months',
                        help="Root of hive-partitioned events")
    parser.add_argument('--products', type=str, default='data/product_information',
                        help="Folder with product_information parquet files")
    parser.add_argument('--chunk-days', type=int, default=7,
                        help="Chunk size in days")
    parser.add_argument('--keep-shards', action='store_true',
                        help="Keep intermediate shards after merge")

    args = parser.parse_args()

    start_d = date.fromisoformat(args.start)
    end_d = date.fromisoformat(args.end)
    events_root = Path(args.input)
    products_root = Path(args.products)
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    shards_dir = out_path.with_name(out_path.stem + '.parts')
    if shards_dir.exists():
        shutil.rmtree(shards_dir)
    shards_dir.mkdir(parents=True)

    print(f"Building intent sessions from {args.start} to {args.end}, gap={args.gap_min}min, chunk={args.chunk_days}d")

    chunk_idx = 0
    cur = start_d
    while cur <= end_d:
        chunk_end = min(cur + timedelta(days=args.chunk_days - 1), end_d)
        shard_path = shards_dir / f'part_{chunk_idx:03d}.parquet'
        print(f"[{chunk_idx:03d}] {cur} ... {chunk_end} -> {shard_path.name}", flush=True)

        lf = build_chunk_lazy(events_root, products_root, cur, chunk_end, args.gap_min)
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

    print(f"Merging {chunk_idx} shards into {out_path} ...")
    pl.scan_parquet(str(shards_dir / '*.parquet')).sink_parquet(out_path)

    if not args.keep_shards:
        shutil.rmtree(shards_dir)
    else:
        print(f"Shards kept at {shards_dir}")

    print(f"Done. Result saved to {out_path}")


if __name__ == '__main__':
    main()
