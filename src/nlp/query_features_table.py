import os
import re
import pandas as pd
import pymorphy3
import fasttext
from symspellpy import SymSpell, Verbosity
from normalize_query import normalize_query


BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))) #os lib used. Can be rewritten to Path
DATA_DIR = os.path.join(BASE_DIR, "data")

PRODUCT_INFO_PATH = os.path.join(DATA_DIR, "product_information_norm.parquet")
DOMAIN_VOCAB_PATH = os.path.join(DATA_DIR, "domain_vocab.parquet")
USER_ACTIONS_PATH = os.path.join(DATA_DIR, "user_actions_3_months")
OUTPUT_PATH = os.path.join(DATA_DIR, "query_features.parquet")

LANG_MODEL_PATH = os.path.join(DATA_DIR, "models", "lid.176.ftz")

STOPWORDS = {"купить","заказать","недорого","дешево","цена","стоимость","скидка","акция","доставка",}
MODEL_WORDS = {"pro","max","mini","ultra","plus","slim","lite","air","se",}

def is_latin(query: str) -> bool:
    has_latin = bool(re.search(r"[a-zA-Z]", query))
    has_cyrillic = bool(re.search(r"[а-яА-ЯёЁ]", query))
    return has_latin and not has_cyrillic


def is_mixed_script(query: str) -> bool:
    has_latin = bool(re.search(r"[a-zA-Z]", query))
    has_cyrillic = bool(re.search(r"[а-яА-ЯёЁ]", query))
    return has_latin and has_cyrillic


MORPH = pymorphy3.MorphAnalyzer()

def lemmatize_token(token: str) -> str:
    if not re.fullmatch(r"[а-яА-ЯёЁ]+", token):
        return token

    return MORPH.parse(token)[0].normal_form

def lemmatize_query(query_norm: str) -> str:
    tokens = query_norm.split()
    lemmas = [lemmatize_token(token) for token in tokens]
    return " ".join(lemmas)


LANG_MODEL = fasttext.load_model(LANG_MODEL_PATH)

def detect_lang_code(query: str):

    if is_mixed_script(query):
        return "mixed"

    query = query.replace("\n", " ").strip()
    labels, probs = LANG_MODEL.predict(query, k=1)
    label = labels[0]
    prob = float(probs[0])
    if prob < 0.5:
        return "unknown"
    return label[9:]


def has_digit(query: str) -> bool:
    return bool(re.search(r"\d", query))


def has_model_pattern(query: str) -> bool:
    tokens = query.split()
    has_number = any(re.search(r"\d", token) for token in tokens)
    has_model_word = any(token in MODEL_WORDS for token in tokens)
    has_letter_digit_token = any(
        re.search(r"[a-zA-Zа-яА-Я]", token) and re.search(r"\d", token)
        for token in tokens
    )
    has_latin_token = any(
        re.fullmatch(r"[a-zA-Z]{2,}", token)
        for token in tokens
    )
    return has_letter_digit_token or (has_number and (has_model_word or has_latin_token))


def tokenize_text(text: str) -> list[str]:
    text = normalize_query(str(text))
    return text.split()


def build_token_set(df: pd.DataFrame, columns: list[str]) -> set[str]:
    tokens = set()
    for column in columns:
        for value in df[column].dropna():
            tokens.update(tokenize_text(value))
    tokens.discard("")
    return tokens


product_info = pd.read_parquet(PRODUCT_INFO_PATH)

BRAND_TOKENS = build_token_set(product_info,["brand"])
CATEGORY_TOKENS = build_token_set(product_info,["category_name", "type"])

domain_vocab = pd.read_parquet(DOMAIN_VOCAB_PATH)

DOMAIN_COUNTER = dict(zip(domain_vocab["token"], domain_vocab["frequency"]))

for token in STOPWORDS | MODEL_WORDS:
    DOMAIN_COUNTER[token] = DOMAIN_COUNTER.get(token, 0) + 1

DOMAIN_TOKENS = set(DOMAIN_COUNTER)


SYM_SPELL = SymSpell(
    max_dictionary_edit_distance=2,
    prefix_length=7
)

for token, frequency in DOMAIN_COUNTER.items():

    SYM_SPELL.create_dictionary_entry(
        token,
        int(frequency)
    )


def has_brand_token(tokens: list[str]) -> bool:
    return any(token in BRAND_TOKENS for token in tokens)


def has_category_token(tokens: list[str]) -> bool:
    return any(token in CATEGORY_TOKENS for token in tokens)


def typo_features(tokens: list[str]) -> tuple[bool, float, int | None]:
    typo_distances = []

    for token in tokens:
        if token in DOMAIN_TOKENS or token.isdigit() or len(token) <= 2:
            continue

        suggestions = SYM_SPELL.lookup(
            token,
            Verbosity.CLOSEST,
            max_edit_distance=2
        )
        if not suggestions:
            continue

        best_suggestion = suggestions[0]
        distance = best_suggestion.distance

        if 0 < distance <= 2:
            typo_distances.append(distance)

    has_typo_value = len(typo_distances) > 0
    typo_token_share = len(typo_distances) / len(tokens)
    min_edit_distance = min(typo_distances) if typo_distances else None

    return has_typo_value, typo_token_share, min_edit_distance


def is_stopword_only(tokens: list[str]) -> bool:
    if not tokens:
        return False

    return all(token in STOPWORDS for token in tokens)


def coloms_of_features(query_norm: str) -> dict:
    tokens = query_norm.split()

    has_typo_value, typo_token_share, min_edit_distance = typo_features(tokens)

    return {
        "query_norm": query_norm,
        "query_lemma": lemmatize_query(query_norm),
        "n_tokens": len(tokens),
        "n_chars": len(query_norm),
        "is_latin": is_latin(query_norm),
        "is_mixed_script": is_mixed_script(query_norm),
        "lang_code": detect_lang_code(query_norm),
        "has_brand_token": has_brand_token(tokens),
        "has_category_token": has_category_token(tokens),
        "has_digit": has_digit(query_norm),
        "has_model_pattern": has_model_pattern(query_norm),
        "has_typo": has_typo_value,
        "typo_token_share": typo_token_share,
        "min_edit_distance": min_edit_distance,
        "is_stopword_only": is_stopword_only(tokens),
    }


unique_query_norms = set()

for file in os.listdir(USER_ACTIONS_PATH):
    SEARCH_FOLDER_PATH = os.path.join(USER_ACTIONS_PATH, file, "action_type=search")
    for file2 in os.listdir(SEARCH_FOLDER_PATH):
        FULL_PATH = os.path.join(SEARCH_FOLDER_PATH, file2)
        if os.path.splitext(FULL_PATH)[1] == ".parquet":
            df = pd.read_parquet(FULL_PATH)
            query_norms = (
                df["search_query"]
                .fillna("")
                .astype(str)
                .map(normalize_query)
            )
            unique_query_norms.update(query_norms)

unique_query_norms.discard("")
rows = []

for query_norm in sorted(unique_query_norms):
    row = coloms_of_features(query_norm)
    rows.append(row)

query_features = pd.DataFrame(rows)


query_features.to_parquet(OUTPUT_PATH, index=False)

print(query_features.head())
print(query_features.shape)
print(f"Saved to: {OUTPUT_PATH}")