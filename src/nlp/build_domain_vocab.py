import polars as pl
from pathlib import Path


PRODUCTS = Path('../../data/product_information_norm.parquet')

df = pl.read_parquet(str(PRODUCTS))

#объединение всех колонок в одну
df_text = df.select(
    pl.concat_str(
        [
            pl.col("name"),
            pl.col("brand"),
            pl.col("category_name"),
            pl.col("type")
        ],
        separator=" "
    ).alias('text')
)

#токенизация
df_tokens = (
    df_text.select(
        pl.col("text")
        .str.split(" ")
        .alias("token")
    ).explode("token")
    .with_columns(
        pl.col("token")
        .str.strip_chars(".,!?;:\"'()[]{}-")
    )
    .filter(pl.col("token") != "")
)


#подсчет частот
vocab = (
    df_tokens
    .group_by("token")
    .len()
    .rename({'len' : "frequency"})
)

#записываю доменный словарь
DOMAIN_VOCAB = Path("../../data/domain_vocab.parquet")
vocab.write_parquet(DOMAIN_VOCAB)


