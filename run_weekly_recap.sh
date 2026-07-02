#!/usr/bin/env bash
# 주간 track record 회고: '지난주 먼저 짚은 종목 + 그 후 컨센서스 변화' (KST 일요일 09:00)
cd /home/opentest/server/tele
source venv/bin/activate
mkdir -p logs
exec >> "logs/weekly_recap_$(date +%Y%m%d).log" 2>&1
echo "==== $(date '+%Y-%m-%d %H:%M:%S') start ===="
python weekly_recap.py
echo "==== $(date '+%Y-%m-%d %H:%M:%S') end ===="
