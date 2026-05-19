#!/usr/bin/env bash
cd /home/opentest/server/tele
source venv/bin/activate
mkdir -p logs
exec >> "logs/cron_$(date +%Y%m%d).log" 2>&1
echo "==== $(date '+%Y-%m-%d %H:%M:%S') start ===="
python main.py
echo "==== $(date '+%Y-%m-%d %H:%M:%S') end ===="
