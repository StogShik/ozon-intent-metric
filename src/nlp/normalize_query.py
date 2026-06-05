import re


def normalize_query(query : str) -> str:
    '''
    returns a normalized query
    :param query: query that needs to be normalized
    :return: normalized query
    '''
    query = " ".join(query.split()) # убираю лишнии проблемы из начала, конца и несколько подряд в середине
    query = query.lower() # привожу все к нижнему регистру
    query = query.replace('ё', "е") # замена ё -> е
    query = re.sub(r"([a-zа-я])\1{2,}", r"\1\1", query)#маскимум 2 одинаковые буквы подряд
    return query


#тесты
def test_normalize():
    assert normalize_query("  Барбекю   СОУС  ") == "барбекю соус"
    assert normalize_query("ЁЁЁ") == "ее"
    assert normalize_query("соооус") == "сооус"
    assert normalize_query("100000") == "100000"
    assert normalize_query("1111") == "1111"


