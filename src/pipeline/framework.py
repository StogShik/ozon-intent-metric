from __future__ import annotations

from pathlib import Path
from typing import Optional

class MetricFramework:
    def __init__(
            self,
            input_path: str | Path, #Путь к входным данным.
            output_path: str | Path, #Куда сохранить результат
            period: str, #Период, за который считаем метрику
            product_path: Optional[str | Path] = None, #Путь к базе товаров, если она нужна
            batch_size: int = 100_000, #Размер батча. То есть сколько строк обрабатывать за один кусок.
    ):
        #сохраняем аргументы внутрь объекта
        self.input_path = Path(input_path)
        self.output_path = Path(output_path)
        self.period = period
        self.product_path = Path(product_path) if product_path is not None else None
        self.batch_size = batch_size
        self._validate_paths()
