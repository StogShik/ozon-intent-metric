from sentence_transformers import SentenceTransformer
from pathlib import Path
import polars as pl
import numpy as np


model = SentenceTransformer("intfloat/multilingual-e5-small", device="cpu")

PRODUCTS = Path('../../data/query_features.parquet')

df = pl.read_parquet(str(PRODUCTS))

#беру колонку query_norm, удаляю строки с null, делаю уникальными и делаю из этого список
queries = (
    df
    .select("query_norm")
    .drop_nulls()
    .unique()
)["query_norm"].to_list()

#это нужно чтобы модель работала лучше, т к она обучалась на двух типах query/passage
# если не добавить -> качество эмбендингов хуже, запрос может попасть не в то пространство
texts = [f"query: {q}" for q in queries]

vecs = model.encode(
    texts, #входные данные которые будем превращать в эмбединги
    batch_size=64,
    normalize_embeddings=True, #нормализация векторов, делает их размер равный 1, удобно для проверки на схожесть
    show_progress_bar=True, #мне так удобнее видеть процесс подсчета)))
    convert_to_numpy=True, #в формате numpy-массива удобнее/быстрее производить вычесления/сохранять
).astype(np.float32) #привожу к типу float32

#сохраняю изменения и записываю в файл
result = pl.DataFrame({
    "query_norm": queries,
    "vec": vecs.tolist(),
})
result.write_parquet("../../data/query_embeddings.parquet")