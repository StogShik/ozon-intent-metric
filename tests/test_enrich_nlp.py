from __future__ import annotations

import polars as pl
import pytest

from enrich_sessions_nlp import (
    NLP_COLUMNS,
    build_product_articles,
    build_product_text_base,
    enrich_with_nlp,
    validate_enriched,
)


@pytest.fixture
def products() -> pl.DataFrame:
    return pl.DataFrame({
        "item_id": [123456, 777],
        "name": ["Молоко  Простоквашино", "Ёлка новогодняя 180см"],
        "brand": ["Простоквашино", None],
        "type": ["молоко", "елка"],
        "category_name": ["Молочные продукты", "Товары для праздника"],
    })


def make_sessions(rows: list[dict]) -> pl.DataFrame:
    defaults = {"n_unique_queries": 1, "n_click": 1, "reached_cart": True}
    return pl.DataFrame([{**defaults, **r} for r in rows])


def test_query_clean_normalization(products):
    out = enrich_with_nlp(
        make_sessions([{"first_query": "  МОЛОКО   ПростоквашИно "},
                       {"first_query": "Ёлка"}]),
        products,
    )
    assert out["query_clean"].to_list() == ["молоко простоквашино", "елка"]


def test_query_len_words(products):
    out = enrich_with_nlp(
        make_sessions([
            {"first_query": "красная ёлка"},
            {"first_query": ""},
            {"first_query": None},
            {"first_query": "   "},
        ]),
        products,
    )
    assert out["query_len_words"].to_list() == [2, 0, 0, 0]


def test_is_in_product_base_matches_any_catalog_text(products):
    out = enrich_with_nlp(
        make_sessions([
            {"first_query": "Молоко Простоквашино"},
            {"first_query": "простоквашино"},
            {"first_query": "Молочные продукты"},
            {"first_query": "велосипед"},
        ]),
        products,
    )
    assert out["is_in_product_base"].to_list() == [True, True, True, False]


def test_is_article_matches_item_id(products):
    out = enrich_with_nlp(
        make_sessions([{"first_query": "123456"}, {"first_query": "999999"}]),
        products,
    )
    assert out["is_article"].to_list() == [True, False]


def test_score_easy_session_is_zero(products):
    out = enrich_with_nlp(
        make_sessions([{
            "first_query": "Молоко Простоквашино",
            "n_unique_queries": 1, "n_click": 3, "reached_cart": True,
        }]),
        products,
    )
    assert out["query_stratification_score"].to_list() == [0]


def test_score_article_discount(products):
    out = enrich_with_nlp(
        make_sessions([{
            "first_query": "123456",
            "n_unique_queries": 1, "n_click": 0, "reached_cart": False,
        }]),
        products,
    )
    assert out["query_stratification_score"].to_list() == [1]


def test_score_clipped_to_five(products):
    out = enrich_with_nlp(
        make_sessions([{
            "first_query": "очень длинный запрос про красный велосипед детский горный",
            "n_unique_queries": 25, "n_click": 0, "reached_cart": False,
        }]),
        products,
    )
    assert out["query_stratification_score"].to_list() == [5]


def test_product_text_base_unique_and_clean(products):
    base = build_product_text_base(products)
    texts = set(base["query_clean"].to_list())
    assert "молоко простоквашино" in texts
    assert "елка новогодняя 180см" in texts
    assert "" not in texts
    assert base.height == base.unique().height


def test_product_articles_are_strings(products):
    arts = set(build_product_articles(products)["query_clean"].to_list())
    assert arts == {"123456", "777"}


def test_validate_enriched_raises_on_missing_column(products):
    out = enrich_with_nlp(make_sessions([{"first_query": "молоко"}]), products)
    with pytest.raises(ValueError, match="missing columns"):
        validate_enriched(out.drop("query_stratification_score"))
    assert set(NLP_COLUMNS) <= set(out.columns)
