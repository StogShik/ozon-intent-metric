# src/nlp — NLP team

Доменный словарь, нормализация запросов, эмбеддинги, NLP-фичи запросов.

## Скрипты

- `normalize_query.py` — нормализация поискового запроса (lower, strip, схлопывание пробелов).
- `normalize_product_information.py` — нормализация `product_information` (lemma/tokens из name+brand+type).
- `build_domain_vocab.py` — `data/domain_vocab.parquet` (~100-300k токенов).
- `create_query_embeddings.py` — эмбеддинги уникальных запросов (multilingual-e5-small).
- `query_features_table.py` — `data/query_features.parquet` (по запросу: n_tokens, lang, has_typo, query_stratification_score, is_in_product_base).
- `post-stratification.ipynb` — research по NLP-стратификации.

## Артефакты на диске

- `data/domain_vocab.parquet`
- `data/product_information_norm.parquet`
- `data/query_features.parquet` ← главный артефакт для downstream
- `data/models/lid.176.ftz` (fasttext lang ID)

Большие parquet'ы в `.gitignore` — пересоберите локально через скрипты выше.

## Координация

- **Pipeline**: `data/query_features.parquet` потребляется в `build_day_summary.py` для `complex_query_rate`, `in_product_base_rate`. Join по `normalize_query(first_query)`.
- **Metric**: NLP-фичи pending в `configs/weights.yaml` (секция `nlp_pending`), будут включены после стабильной поставки от pipeline.
