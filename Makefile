PY ?= python

SESSIONS      = data/intent_sessions_full.parquet
SESSIONS_NLP  = data/intent_sessions_with_query_features.parquet
PRODUCTS      = data/product_information
DAY_SUMMARY   = data/day_summary_full.parquet
METRICS       = data/daily_metrics_full.parquet
BASELINE      = --baseline-start 2024-03-01 --baseline-end 2024-03-30

.PHONY: help install test smoke enrich verify-enrich pipeline screenshots deck ui all

help:
	@echo "make install        — зависимости в текущее окружение"
	@echo "make test           — pytest-сетка (синтетика, без data/, <5 сек)"
	@echo "make smoke          — smoke-test production-API метрики"
	@echo "make enrich         — NLP-обогащение sessions (вход weak-spots/examples)"
	@echo "make verify-enrich  — сверка порта NLP-обогащения с готовым артефактом"
	@echo "make pipeline       — Health Score + RCA-артефакты из готовых sessions"
	@echo "make screenshots    — скриншоты дашборда для слайда (headless Chrome, macOS)"
	@echo "make deck           — презентация PDF + графики из посчитанных артефактов"
	@echo "make ui             — дашборд на http://127.0.0.1:8760"
	@echo "make all            — test + smoke + enrich + pipeline"

install:
	$(PY) -m pip install -r requirements.txt

test:
	$(PY) -m pytest tests/ -q

smoke:
	$(PY) src/metric/_smoke_test.py

enrich:
	$(PY) src/pipeline/enrich_sessions_nlp.py \
	  --sessions $(SESSIONS) --products $(PRODUCTS) --out $(SESSIONS_NLP)

verify-enrich:
	$(PY) src/pipeline/enrich_sessions_nlp.py \
	  --sessions $(SESSIONS) --products $(PRODUCTS) --verify-against $(SESSIONS_NLP)

pipeline:
	$(PY) src/pipeline/run_daily_pipeline.py \
	  --in-sessions $(SESSIONS) \
	  --day-summary-out $(DAY_SUMMARY) \
	  --daily-metrics-out $(METRICS) \
	  $(BASELINE) \
	  --segments-sessions $(SESSIONS_NLP)

ui:
	$(PY) src/web/server.py --metrics $(METRICS) --port 8760

all: test smoke enrich pipeline
