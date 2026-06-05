import polars as pl
from pathlib import Path


root = Path("../../data/intent_sessions_full.parquet")
df = pl.read_parquet(str(root))
#считаю Effort по формуле Effort = log1p(n_unique_queries) + log1p(n_view) + log1p(duration_s)
Effort = pl.col("n_unique_queries").log1p() + pl.col("n_view").log1p() + pl.col("duration_s").log1p()
#считаю IRS по формуле IRS = 1/(1+Effort)
IRS = 1 / (1 + Effort)
#записываю в новый датафрейм и сохраняю в новую таблицу
df_res = df.with_columns([
    Effort.alias("Effort"),
    IRS.alias("IRS")
])
path_of_intent_sessions_scored = Path("../../data/intent_sessions_scored.parquet")
df_res.write_parquet(path_of_intent_sessions_scored)