"""
NLP-обогащение intent_sessions: добавляет к каждой сессии признаки первого запроса.

Колонки на выходе:

  query_clean                : Utf8    — нормализованный first_query
                                         (lower, ё в е, схлопывание пробелов, strip)
  query_len_words            : UInt32  — длина first_query в словах (0 если пусто)
  is_in_product_base         : Boolean — query_clean точно совпадает с одним из
                                         нормализованных name/brand/type/category_name
                                         каталога (миссы ≈ опечатки/транслит/
                                         нестандартные формулировки)
  is_article                 : Boolean — query_clean совпадает с item_id (артикул)
  query_stratification_score : Int8    — скор сложности сессии 0–5 (выше = сложнее)

⚠ Важно: score частично зависит от ИСХОДА сессии (слагаемые `n_click == 0` и
`not reached_cart`), то есть коррелирует с провалом по построению. Это скор
«насколько тяжёлой оказалась сессия», а не чистая сложность запроса.

Выход потребляется:
  - run_daily_pipeline.py --segments-sessions … (structural RCA / weak-spots)
  - src/web/server.py --sessions …             (examples в дашборде)

CLI:
  python src/pipeline/enrich_sessions_nlp.py \
    --sessions data/intent_sessions_full.parquet \
    --products data/product_information \
    --out data/intent_sessions_with_query_features.parquet

  # сверка результата с существующим артефактом (ничего не пишет):
  python src/pipeline/enrich_sessions_nlp.py \
    --sessions data/intent_sessions_full.parquet \
    --products data/product_information \
    --verify-against data/intent_sessions_with_query_features.parquet
"""
from __future__ import annotations

import argparse
from pathlib import Path

import polars as pl

NLP_COLUMNS: tuple[str, ...] = (
    "query_clean",
    "query_len_words",
    "is_in_product_base",
    "is_article",
    "query_stratification_score",
)


def _normalize(col: pl.Expr) -> pl.Expr:
    """Та же нормализация, что в ноутбуке и src/nlp/normalize_query.py:
    lower, ё·е, схлопывание пробелов, strip."""
    return (
        col.str.to_lowercase()
        .str.replace_all("ё", "е")
        .str.replace_all(r"\s+", " ")
        .str.strip_chars()
    )


def build_product_text_base(products: pl.DataFrame) -> pl.DataFrame:
    """Уникальные нормализованные текстовые значения каталога
    (name, brand, type, category_name) — база для is_in_product_base."""
    return (
        products.select(
            pl.concat_list(
                [
                    pl.col("name").cast(pl.Utf8),
                    pl.col("brand").cast(pl.Utf8),
                    pl.col("type").cast(pl.Utf8),
                    pl.col("category_name").cast(pl.Utf8),
                ]
            ).alias("text_values")
        )
        .explode("text_values")
        .select(_normalize(pl.col("text_values")).alias("query_clean"))
        .filter(pl.col("query_clean").is_not_null() & (pl.col("query_clean") != ""))
        .unique()
    )


def build_product_articles(products: pl.DataFrame) -> pl.DataFrame:
    """Уникальные item_id как строки — база для is_article."""
    return (
        products.select(
            pl.col("item_id").cast(pl.Utf8).str.strip_chars().alias("query_clean")
        )
        .filter(pl.col("query_clean").is_not_null() & (pl.col("query_clean") != ""))
        .unique()
    )


def enrich_with_nlp(sessions: pl.DataFrame, products: pl.DataFrame) -> pl.DataFrame:
    """Pure-функция: intent_sessions + каталог · sessions c NLP_COLUMNS.

    Ничего не пишет на диск; вход не мутируется.
    """
    df = sessions.with_columns(_normalize(pl.col("first_query")).alias("query_clean"))

    df = df.with_columns(
        pl.when(pl.col("query_clean").is_null() | (pl.col("query_clean") == ""))
        .then(0)
        .otherwise(pl.col("query_clean").str.split(" ").list.len())
        .alias("query_len_words")
    )

    df = (
        df.join(
            build_product_text_base(products).with_columns(
                pl.lit(True).alias("is_in_product_base")
            ),
            on="query_clean",
            how="left",
        )
        .with_columns(pl.col("is_in_product_base").fill_null(False))
    )

    df = (
        df.join(
            build_product_articles(products).with_columns(
                pl.lit(True).alias("is_article")
            ),
            on="query_clean",
            how="left",
        )
        .with_columns(pl.col("is_article").fill_null(False))
    )

    df = df.with_columns(
        (
            pl.when(pl.col("n_unique_queries") <= 1).then(0)
            .when(pl.col("n_unique_queries") <= 3).then(1)
            .when(pl.col("n_unique_queries") <= 7).then(2)
            .when(pl.col("n_unique_queries") <= 19).then(3)
            .otherwise(4)
            + pl.when(pl.col("query_len_words") <= 2).then(0)
            .when(pl.col("query_len_words") <= 4).then(1)
            .when(pl.col("query_len_words") <= 6).then(2)
            .otherwise(3)
            + pl.when(~pl.col("is_in_product_base")).then(1).otherwise(0)
            + pl.when(pl.col("n_click") == 0).then(1).otherwise(0)
            + pl.when(~pl.col("reached_cart")).then(1).otherwise(0)
            - pl.when(pl.col("is_article")).then(2).otherwise(0)
        )
        .clip(0, 5)
        .cast(pl.Int8)
        .alias("query_stratification_score")
    )

    return validate_enriched(df)


def validate_enriched(df: pl.DataFrame) -> pl.DataFrame:
    """Контрактная проверка выхода: колонки на месте, score в [0, 5]."""
    missing = [c for c in NLP_COLUMNS if c not in df.columns]
    if missing:
        raise ValueError(f"enrich_with_nlp output is missing columns: {missing}")
    score_bounds = df.select(
        pl.col("query_stratification_score").min().alias("lo"),
        pl.col("query_stratification_score").max().alias("hi"),
    ).row(0)
    if score_bounds[0] is not None and (score_bounds[0] < 0 or score_bounds[1] > 5):
        raise ValueError(
            f"query_stratification_score out of [0, 5]: {score_bounds}"
        )
    return df


def verify_against(result: pl.DataFrame, reference_path: Path) -> bool:
    """Сверяет результат с эталонным артефактом строка к строке.

    Сравнивает NLP_COLUMNS после выравнивания по (user_id, session_idx).
    Колонки референса, которые эта функция не производит (например,
    is_article_right), игнорируются с предупреждением.
    """
    ref = pl.read_parquet(str(reference_path))
    keys = ["user_id", "session_idx"]
    print(f"verify: result {result.height:,} rows vs reference {ref.height:,} rows")
    if result.height != ref.height:
        print("FAIL: row count mismatch")
        return False

    extra_ref = [c for c in ref.columns if c not in result.columns]
    if extra_ref:
        print(f"note: reference-only columns ignored: {extra_ref}")

    cmp_cols = list(NLP_COLUMNS)
    a = result.select(keys + cmp_cols).sort(keys)
    b = ref.select(keys + cmp_cols).sort(keys)
    ok = True
    for c in cmp_cols:
        diff = int(
            a.select(pl.col(c).alias("a"))
            .hstack(b.select(pl.col(c).alias("b")))
            .filter(pl.col("a").ne_missing(pl.col("b")))
            .height
        )
        status = "OK " if diff == 0 else "FAIL"
        print(f"  [{status}] {c}: {diff:,} mismatched rows")
        ok &= diff == 0
    print("verify:", "PASSED — порт воспроизводит артефакт" if ok else "FAILED")
    return ok


def main() -> None:
    parser = argparse.ArgumentParser(description="NLP-enrich intent sessions.")
    parser.add_argument("--sessions", default="data/intent_sessions_full.parquet")
    parser.add_argument("--products", default="data/product_information")
    parser.add_argument("--out", default="",
                        help="Куда писать обогащённый parquet. Пусто = не писать.")
    parser.add_argument("--verify-against", default="",
                        help="Эталонный parquet для сверки результата.")
    args = parser.parse_args()

    print(f"Reading sessions from {args.sessions} ...")
    sessions = pl.read_parquet(args.sessions)
    print(f"Reading products from {args.products} ...")
    products = pl.read_parquet(args.products)

    print(f"Enriching {sessions.height:,} sessions ...")
    result = enrich_with_nlp(sessions, products)

    if args.verify_against:
        ok = verify_against(result, Path(args.verify_against))
        if not ok:
            raise SystemExit(1)

    if args.out:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        result.write_parquet(out)
        print(f"Saved {result.height:,} rows to {out}")
    elif not args.verify_against:
        print("Nothing to do: pass --out and/or --verify-against.")


if __name__ == "__main__":
    main()
