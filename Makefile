PY ?= python

SESSIONS      = data/intent_sessions_full.parquet
SESSIONS_NLP  = data/intent_sessions_with_query_features.parquet
PRODUCTS      = data/product_information
DAY_SUMMARY   = data/day_summary_full.parquet
METRICS       = data/daily_metrics_full.parquet
BASELINE      = --baseline-start 2024-03-01 --baseline-end 2024-03-30

.PHONY: help install test smoke enrich verify-enrich pipeline ui all


install:
	$(PY) -m pip install -r requirements.txt

test:
	$(PY) -m pytest tests/ -q

smoke:
	$(PY) src/metric/_smoke_test.py

enrich:
	$(PY) src/pipeline/enrich_sessions_nlp.py --sessions $(SESSIONS) --products $(PRODUCTS) --out $(SESSIONS_NLP)

verify-enrich:
	$(PY) src/pipeline/enrich_sessions_nlp.py --sessions $(SESSIONS) --products $(PRODUCTS) --verify-against $(SESSIONS_NLP)

pipeline:
	$(PY) src/pipeline/run_daily_pipeline.py \
	  --in-sessions $(SESSIONS) \
	  --day-summary-out $(DAY_SUMMARY) \
	  --daily-metrics-out $(METRICS) \
	  $(BASELINE) \
	  --segments-sessions $(SESSIONS_NLP)

ui:
	$(PY) src/web/server.py --metrics $(METRICS) --port 8080

all: test smoke enrich pipeline
