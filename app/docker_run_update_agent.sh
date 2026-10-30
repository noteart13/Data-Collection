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
  if [ -f app/data/urls_agent.json ]; then
    echo "Removing stale data/urls_agent.json"
    rm -f ./data/urls_agent.json
  fi

  # 2) Retry fetching URLs until it writes the JSON successfully
  echo "Running docker_get_url_agent.py (will retry on failure)..."
  until python app/docker_get_url_agent.py; do
    echo " ⚠️  docker_get_url_agent.py failed — retrying in ${RETRY_DELAY}s"
    sleep "${RETRY_DELAY}"
  done

  # 3) Double‐check that the file is there
  if [ ! -f app/data/urls_agent.json ]; then
    echo " ⚠️  Expected ./data/urls_agent.json not found after success. Restarting cycle."
    sleep 10
    continue
  fi

  echo " ✅  urls_agent.json generated (count=$(jq length data/urls_agent.json))"

  # 4) Run follow-up scripts
  echo "Running docker_update_agent.py"
  python app/docker_update_agent.py


  # 5) Wait before the next cycle
  echo "Cycle complete. Sleeping for ${CYCLE_DELAY}s..."
  sleep "${CYCLE_DELAY}"
done
