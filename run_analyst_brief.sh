#!/usr/bin/env bash
# 워치리스트 종목 일괄 애널리스트 브리프 생성 (Neon analyst_brief에 적재)
cd /home/opentest/server/tele
source venv/bin/activate
mkdir -p logs
exec >> "logs/analyst_$(date +%Y%m%d).log" 2>&1
echo "==== $(date '+%Y-%m-%d %H:%M:%S') start ===="
python analyst_brief.py
echo "==== $(date '+%Y-%m-%d %H:%M:%S') end ===="
