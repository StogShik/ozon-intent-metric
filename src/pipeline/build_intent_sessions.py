from __future__ import annotations

import argparse
import shutil
from datetime import date, timedelta
from pathlib import Path

import polars as pl


def build_intent_sessions_lazy(
    events_root: str | Path,
    products_root: str | Path,
    start_date: date,
    end_date: date,
    gap_min: int = 35,
) -> pl.LazyFrame:
    """Строит одну строку intent-сессии пользователя из raw logs."""
    events_root = Path(events_root)
    products_root = Path(products_root)
    gap_seconds = gap_min * 60

    events = (
        pl.scan_parquet(str(events_root / "**" / "*.parquet"), hive_partitioning=True)
        .filter((pl.col("date") >= start_date) & (pl.col("date") <= end_date))
        .select(
            "user_id",
            "date",
            "timestamp",
            "action_type",
            "widget_name",
            "search_query",
            "item_id",
        )
    )

    products = (
        pl.scan_parquet(str(products_root / "*.parquet"))
        .select("item_id", "brand", "category_name")
    )

    events_with_sessions = (
        events.sort(["user_id", "timestamp"])
        .with_columns(
            (
                pl.col("timestamp")
                .diff()
                .over("user_id")
                .dt.total_seconds()
                .fill_null(gap_seconds + 1)
                > gap_seconds
            ).alias("_new_session")
        )
        .with_columns(
            pl.col("_new_session")
            .cast(pl.Int32)
            .cum_sum()
            .over("user_id")
            .alias("session_idx")
        )
        .join(products, on="item_id", how="left")
    )

    sessions = (
        events_with_sessions.group_by(["user_id", "session_idx"])
        .agg(
            pl.col("timestamp").min().alias("ts_start"),
            pl.col("timestamp").max().alias("ts_end"),
            pl.len().cast(pl.UInt32).alias("n_events"),
            (pl.col("action_type") == "search").sum().cast(pl.UInt32).alias("n_search"),
            (pl.col("action_type") == "view").sum().cast(pl.UInt32).alias("n_view"),
            (pl.col("action_type") == "click").sum().cast(pl.UInt32).alias("n_click"),
            (pl.col("action_type") == "to_cart").sum().cast(pl.UInt32).alias("n_cart"),
            (pl.col("action_type") == "favorite").sum().cast(pl.UInt32).alias("n_fav"),
            pl.col("search_query")
            .filter(pl.col("action_type") == "search")
            .drop_nulls()
            .n_unique()
            .cast(pl.UInt32)
            .alias("n_unique_queries"),
            pl.col("search_query")
            .filter(pl.col("action_type") == "search")
            .drop_nulls()
            .first()
            .alias("first_query"),
            pl.col("widget_name")
            .filter(pl.col("action_type") == "search")
            .drop_nulls()
            .first()
            .alias("first_widget"),
            pl.col("category_name").drop_nulls().n_unique().cast(pl.UInt32).alias("n_unique_categories"),
            pl.col("category_name").drop_nulls().mode().first().alias("top_category_in_session"),
            pl.col("timestamp")
            .filter(pl.col("action_type") == "to_cart")
            .min()
            .alias("ts_first_cart"),
        )
        .with_columns(
            (pl.col("ts_end") - pl.col("ts_start")).dt.total_seconds().alias("duration_s"),
            (pl.col("n_cart") > 0).alias("reached_cart"),
        )
        .with_columns(
            (pl.col("ts_first_cart") - pl.col("ts_start"))
            .dt.total_seconds()
            .alias("time_to_cart_s")
        )
        .filter(pl.col("n_search") > 0)
    )
    return sessions


def build_intent_sessions(
    events_root: str | Path,
    products_root: str | Path,
    start_date: date,
    end_date: date,
    output_path: str | Path,
    gap_min: int = 35,
    chunk_days: int = 1,
) -> Path:
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    parts_dir = output_path.with_name(f"{output_path.stem}.parts")
    if parts_dir.exists():
        shutil.rmtree(parts_dir)
    parts_dir.mkdir(parents=True)

    part_paths: list[Path] = []
    current = start_date
    part_idx = 0
    while current <= end_date:
        chunk_end = min(current + timedelta(days=chunk_days - 1), end_date)
        part_path = parts_dir / f"part_{part_idx:03d}.parquet"
        print(f"  sessions chunk {current}..{chunk_end} -> {part_path.name}", flush=True)
        chunk_lf = build_intent_sessions_lazy(
            events_root=events_root,
            products_root=products_root,
            start_date=current,
            end_date=chunk_end,
            gap_min=gap_min,
        )
        try:
            chunk_lf.sink_parquet(part_path)
        except FileNotFoundError:
            chunk_lf.collect(engine="streaming").write_parquet(part_path)
        part_paths.append(part_path)
        current = chunk_end + timedelta(days=1)
        part_idx += 1

    if part_paths:
        pl.scan_parquet(str(parts_dir / "*.parquet")).sink_parquet(output_path)
    else:
        pl.DataFrame().write_parquet(output_path)
    shutil.rmtree(parts_dir)
    return output_path


def main() -> None:
    parser = argparse.ArgumentParser(description="Build intent sessions from raw Ozon action logs.")
    parser.add_argument("--start", required=True, help="Start date, inclusive: YYYY-MM-DD")
    parser.add_argument("--end", required=True, help="End date, inclusive: YYYY-MM-DD")
    parser.add_argument("--input", default="data/user_actions_3_months", help="Raw hive-partitioned events root")
    parser.add_argument("--products", default="data/product_information", help="Product info parquet directory")
    parser.add_argument("--out", default="data/intent_sessions.parquet", help="Output sessions parquet")
    parser.add_argument("--gap-min", type=int, default=35, help="Session inactivity threshold in minutes")
    parser.add_argument("--chunk-days", type=int, default=1, help="Build sessions in N-day chunks")
    args = parser.parse_args()

    out = build_intent_sessions(
        events_root=args.input,
        products_root=args.products,
        start_date=date.fromisoformat(args.start),
        end_date=date.fromisoformat(args.end),
        output_path=args.out,
        gap_min=args.gap_min,
        chunk_days=args.chunk_days,
    )
    print(f"Saved intent sessions to {out}")


if __name__ == "__main__":
    main()
