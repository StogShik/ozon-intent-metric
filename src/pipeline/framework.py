from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Optional

import polars as pl

from build_intent_sessions import build_chunk_lazy
from build_day_summary import build_sessions, build_daily_category_summary

class MetricFramework:
    def __init__(
            self,
            input_path: str | Path,
            output_path: str | Path,
            period: str,
            session_period: str,
            product_path: Optional[str | Path] = None,
            batch_size: int = 100_000,
    ):

        self.input_path = Path(input_path)
        self.output_path = Path(output_path)
        self.period = period
        self.session_period = session_period
        self.product_path = Path(product_path) if product_path is not None else None
        self.batch_size = batch_size

        self.sessions_file = self.output_path / 'intent_sessions.parquet'
        self.summaries_dir = self.output_path / 'daily_summaries'

        self.gap_min = int(str(self.session_period).replace('m', '').replace('M', '').strip())

    def load(self, start_date: str | date, end_date: str | date):
        """
        2.2) Пайплайн полной загрузки (Исторический пересчет).
        Сырые данные -> Разбиение на сессии -> Сбор day_summary.parquet -> Объединение.
        """
        print(f"\n{'='*40}")
        print(" ЗАПУСК ПАЙПЛАЙНА: LOAD (Полная загрузка)")
        print(f"{'='*40}")

        start_d = date.fromisoformat(start_date) if isinstance(start_date, str) else start_date
        end_d = date.fromisoformat(end_date) if isinstance(end_date, str) else end_date


        print("Шаг 1: Формирование сессий из сырых данных...")
        build_sessions(
            events_root=self.input_path,
            products_root=self.product_path,
            start_d=start_d,
            end_d=end_d,
            out_path=self.sessions_file,
            gap_min=self.gap_min,
            chunk_days=7,
            rewrite=True
        )


        print("\nШаг 2: Формирование витрины day_summary...")
        sessions_lf = pl.scan_parquet(self.sessions_file)
        build_daily_category_summary(sessions_lf, self.summaries_dir)

        print("\n Пайплайн LOAD успешно завершен!")


    def new_day(self, target_date: str | date):
        """
        3) Инкрементальная загрузка за ОДИН день.
        Собирает сессии за день -> Формирует строку дня -> Конкатенирует с основной таблицей.
        """
        t_date = date.fromisoformat(target_date) if isinstance(target_date, str) else target_date
        print(f"\n{'='*40}")
        print(f" ЗАПУСК ПАЙПЛАЙНА: NEW_DAY ({t_date})")
        print(f"{'='*40}")


        print("Шаг 1: Разбиение сырых данных на сессии за день...")
        new_sessions_lf = build_chunk_lazy(
            events_root=self.input_path,
            products_root=self.product_path,
            start_d=t_date,
            end_d=t_date,
            gap_min=self.gap_min
        )
        new_sessions_df = new_sessions_lf.collect()

        if new_sessions_df.height == 0:
            print(f" Нет данных за {t_date}. Пропускаем.")
            return


        print("Шаг 2: Конкатенация с основной исторической таблицей сессий...")
        if self.sessions_file.exists():
            main_df = pl.read_parquet(self.sessions_file)



            main_df = main_df.filter(pl.col('ts_end').dt.date() != t_date)


            combined_df = pl.concat([main_df, new_sessions_df], how="vertical_relaxed")
            combined_df.write_parquet(self.sessions_file)
            print(f"  -> Основная таблица обновлена. Добавлено сессий: {new_sessions_df.height}")
        else:
            print("  -> Основная таблица не найдена. Создаем новую.")
            new_sessions_df.write_parquet(self.sessions_file)


        print("\nШаг 3: Формирование сводки дня (day_summary)...")

        build_daily_category_summary(new_sessions_df.lazy(), self.summaries_dir)

        print(f"\n Пайплайн NEW_DAY ({t_date}) успешно завершен!")
