#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DATA_DIR="$SCRIPT_DIR/data"
EVENTS_DIR="$DATA_DIR/user_actions_3_months"
PRODUCTS_DIR="$DATA_DIR/product_information"
SESSIONS_OUT="$DATA_DIR/intent_sessions.parquet"
SUMMARIES_DIR="$DATA_DIR/daily_summaries"

# Определяем первый и последний доступный день из hive-партиций
START=$(ls "$EVENTS_DIR" | grep -oE '[0-9]{4}-[0-9]{2}-[0-9]{2}' | sort | head -1)
END=$(ls "$EVENTS_DIR"   | grep -oE '[0-9]{4}-[0-9]{2}-[0-9]{2}' | sort | tail -1)

echo "Период: $START → $END"
echo "Сессии  → $SESSIONS_OUT"
echo "Сводки  → $SUMMARIES_DIR"
echo ""

cd "$SCRIPT_DIR/src/pipeline"

"$SCRIPT_DIR/.venv/bin/python" build_day_summary.py \
    --start        "$START" \
    --end          "$END" \
    --input        "$EVENTS_DIR" \
    --products     "$PRODUCTS_DIR" \
    --out-sessions "$SESSIONS_OUT" \
    --out-dir      "$SUMMARIES_DIR"
