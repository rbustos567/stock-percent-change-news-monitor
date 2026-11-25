#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
ENV_FILE="${SCRIPT_DIR}/.env"
WATCHLIST_CONFIG="${SCRIPT_DIR}/watchlist.json"
LOG_DIR="${SCRIPT_DIR}/logs"
LOG_FILE="${LOG_DIR}/stocks_news_trigger.log"
LOCK_FILE="/tmp/stocks_news_trigger_$(printf '%s' "$SCRIPT_DIR" | md5sum | awk '{print $1}').lock"
LOG_MAX_AGE_DAYS=7
PYTHON_BIN="/usr/bin/python3"

mkdir -p "$LOG_DIR"
cd "$SCRIPT_DIR"

if [[ -f "$ENV_FILE" ]]; then
  set -a
  # shellcheck disable=SC1090
  source "$ENV_FILE"
  set +a
fi

exec 9>"$LOCK_FILE"
flock -n 9 || {
  echo "[$(date '+%Y-%m-%d %H:%M:%S')] Another instance is already running, skipping." >> "$LOG_FILE"
  exit 0
}

# Truncate main log if it has not been modified within LOG_MAX_AGE_DAYS
if [[ -f "$LOG_FILE" ]] && find "$LOG_FILE" -mtime +"$LOG_MAX_AGE_DAYS" -print -quit | grep -q .; then
  : > "$LOG_FILE"
  echo "[$(date '+%Y-%m-%d %H:%M:%S')] Cleared $(basename "$LOG_FILE") (older than ${LOG_MAX_AGE_DAYS} days)." >> "$LOG_FILE"
fi

# Delete old log files inside LOG_DIR older than LOG_MAX_AGE_DAYS
find "$LOG_DIR" -type f -name '*.log*' ! -path "$LOG_FILE" -mtime +"$LOG_MAX_AGE_DAYS" -print -exec rm -f {} + | while IFS= read -r deleted_file; do
  echo "[$(date '+%Y-%m-%d %H:%M:%S')] Deleted old log file: $deleted_file" >> "$LOG_FILE"
done

echo "[$(date '+%Y-%m-%d %H:%M:%S')] Starting stocks_news_trigger.py" >> "$LOG_FILE"
"$PYTHON_BIN" "${SCRIPT_DIR}/stocks_news_trigger.py" \
  --env-file "$ENV_FILE" \
  --watchlist-config "$WATCHLIST_CONFIG" \
  --send-email >> "$LOG_FILE" 2>&1
echo "[$(date '+%Y-%m-%d %H:%M:%S')] Finished stocks_news_trigger.py" >> "$LOG_FILE"
