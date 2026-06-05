from pathlib import Path
import polars as pl
import re


def normalize_expr(col : pl.Expr):
    '''
    normalization of expressions
    :param col: the column that needs to be normalized
    :return: normalized column
    '''
    return (
        col
        .str.replace_all(r"\s+", " ") # уменьшает количество подряд идущих пробелов до одного
        .str.strip_chars() # убирает пробелы из начала и конца
        .str.to_lowercase() # приводит все к нижнему регистру
        .str.replace_all('ё', 'е') # деает замену ё -> е
    )


def collapse(s: str) -> str:
    '''
    reduction of consecutive identical characters to two
    :param s:the string in which consecutive characters need to be reduced to two
    :return:a string in which there are no more than two identical characters in a row
    '''
    if s is None:
        return ""
    return re.sub(r"([a-zа-я])\1{2,}", r"\1\1", s)

PRODUCT = Path("../../data/product_information") #исходная папка c product information

df = pl.read_parquet(PRODUCT)
#нормирую все столбцы с типом str
df = df.with_columns(
    normalize_expr(pl.selectors.string())
)
#продолжение нормировки, сокращаю количество одинаковых подряд идущих символов до двух
df = df.with_columns(
    pl.selectors.string().map_elements(collapse, return_dtype=pl.Utf8)
)
#указываю путь и записываю туда отнормированные данные
NEW_PRODUCT = Path("../../data/product_information_norm.parquet")
df.write_parquet(NEW_PRODUCT)