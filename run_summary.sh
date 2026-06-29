#!/usr/bin/env bash
cd /home/opentest/server/tele
source venv/bin/activate
mkdir -p logs
exec >> "logs/cron_$(date +%Y%m%d).log" 2>&1
echo "==== $(date '+%Y-%m-%d %H:%M:%S') start ===="
python main.py
echo "---- 신호 사전계산 (reportportal용) ----"
python tele_signals.py
echo "---- messages 60일 롤링 정리 ----"
python prune_messages.py --days 60
echo "==== $(date '+%Y-%m-%d %H:%M:%S') end ===="
