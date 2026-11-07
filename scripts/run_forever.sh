#!/usr/bin/env bash
# Keep the Receipts agents running: restart on crash, keep the Mac awake, log to logs/agents.log.
#   scripts/run_forever.sh
cd "$(dirname "$0")/.." || exit 1
mkdir -p logs
while true; do
  echo "[$(date '+%F %T')] starting Receipts agents" >> logs/agents.log
  caffeinate -dimsu .venv/bin/python -m receipts.agents.run >> logs/agents.log 2>&1
  echo "[$(date '+%F %T')] agents exited with code $?; restarting in 5s" >> logs/agents.log
  sleep 5
done
