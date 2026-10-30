#!/usr/bin/env bash
set -e  # exit on any error (exceptions: commands in until/while tests)

# how long to wait between retries & cycles
RETRY_DELAY=5
CYCLE_DELAY=60

export TZ='Australia/Brisbane'

while true; do
  TIMESTAMP=$(date '+%Y-%m-%d %H:%M:%S')
  echo "=== Starting new cycle: ${TIMESTAMP} (AEST) ==="

  # 1) Remove old files if they exist
  if [ -f ./data/urls_sale.json ]; then
    echo "Removing stale data/urls_sale.json"
    rm -f ./data/urls_sale.json
  fi
  if [ -f ./data/log_progress.txt ]; then
    echo "Removing stale data/log_progress.txt"
    rm -f ./data/log_progress.txt
  fi
  if [ -f ./data/fail_address.csv ]; then
    echo "Removing stale data/fail_address.csv"
    rm -f ./data/fail_address.csv
  fi

  # 2) Retry fetching URLs until it writes the JSON successfully
  echo "Running docker_get_urls.py (will retry on failure)..."
  until python docker_get_urls.py --status "sale"; do
    echo " ⚠️  docker_get_urls.py failed — retrying in ${RETRY_DELAY}s"
    sleep "${RETRY_DELAY}"
  done

  # 3) Double‐check that the file is there
  if [ ! -f data/urls_sale.json ]; then
    echo " ⚠️  Expected ./data/urls_sale.json not found after success. Restarting cycle."
    sleep 10
    continue
  fi

  echo " ✅  urls_sale.json generated (count=$(jq length data/urls_sale.json))"

  # 4) Run follow-up scripts
  echo "Running docker_change_status.py"
  python property_sale_pipeline.py --type "update"


  # 5) Wait before the next cycle
  echo "Cycle complete. Sleeping for ${CYCLE_DELAY}s..."
  sleep "${CYCLE_DELAY}"
done
