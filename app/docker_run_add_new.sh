#!/usr/bin/env bash
set -euo pipefail

# ---------------- Config ----------------
RETRY_DELAY=${RETRY_DELAY:-5}
CYCLE_DELAY=${CYCLE_DELAY:-60}

# batches to process each cycle
BATCH_COUNT=${BATCH_COUNT:-31}
BATCH_START=${BATCH_START:-0}
BATCH_END=${BATCH_END:-$((BATCH_COUNT-1))}

# concurrency from env; default to 2
MAX_CONCURRENCY=${MAX_CONCURRENCY:-10}

export TZ='Australia/Brisbane'

# Make globs that match nothing expand to nothing (avoid literal *.txt)
shopt -s nullglob

cleanup_glob() {
  local pattern="$1"
  local paths=( $pattern )
  if ((${#paths[@]} > 0)); then
    echo "Removing stale ${pattern}"
    rm -rf "${paths[@]}"
  fi
}

count_urls() {
  if command -v jq >/dev/null 2>&1; then
    jq 'length' ./data/urls_sale_add_new.json 2>/dev/null || echo "?"
  else
    python - <<'PY' || echo "?"
import json
try:
    with open("data/urls_sale_add_new.json") as f:
        print(len(json.load(f)))
except Exception:
    print("?")
PY
  fi
}

run_until_success() {
  # Usage: run_until_success "label for logs" cmd arg1 arg2 ...
  local label="$1"; shift
  echo "Running: ${label} (will retry on failure)…"
  until "$@"; do
    echo " ⚠️  ${label} failed — retrying in ${RETRY_DELAY}s"
    sleep "${RETRY_DELAY}"
  done
  echo " ✅  ${label} succeeded."
}

trap 'echo "Interrupted. Exiting."; exit 1' INT TERM

while true; do
  TIMESTAMP=$(date '+%Y-%m-%d %H:%M:%S')
  echo "=== Starting new cycle: ${TIMESTAMP} (AEST) ==="
  echo "Using MAX_CONCURRENCY=${MAX_CONCURRENCY}  BATCH_START=${BATCH_START}  BATCH_END=${BATCH_END}"

  # 1) Remove old files if they exist
  [ -f ./data/urls_sale_add_new.json ] && { echo "Removing stale data/urls_sale_add_new.json"; rm -f ./data/urls_sale_add_new.json; }
  cleanup_glob "./data/log_progress_step*.txt"
  cleanup_glob "./data/fail_address_add_new.csv"
  cleanup_glob "./data/batch*"

  # 2) Retry fetching URLs until it writes the JSON successfully
  run_until_success "docker_get_urls_add_new.py" python docker_get_urls_add_new.py

  # 3) Double-check that the file is there
  if [ ! -f data/urls_sale_add_new.json ]; then
    echo " ⚠️  Expected ./data/urls_sale_add_new.json not found after success. Restarting cycle."
    sleep 10
    continue
  fi
  echo " ✅  urls_sale.json generated (count=$(count_urls))"

  # 4) Run follow-up scripts per batch (each retried until success)
  for ((batch_order=${BATCH_START}; batch_order<=${BATCH_END}; batch_order++)); do
    run_until_success "docker_step1.py batch ${batch_order}" \
      python docker_step1.py -b "${batch_order}" --status "sale"

    run_until_success "docker_add_new.py batch ${batch_order}" \
      python property_sale_pipeline.py -b "${batch_order}" --type "scrape"
  done

  # 5) Wait before the next cycle
  echo "Cycle complete. Sleeping for ${CYCLE_DELAY}s..."
  sleep "${CYCLE_DELAY}"
done
