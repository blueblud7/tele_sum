#!/usr/bin/env bash
# 일/주/월 종목·섹터 TOP10 빈도+뷰 브리프 (KST 07:30 — 07:00 인제스트 후, 08:00 전)
# 오늘 해당되는 주기를 자동 산출: 일간(매일)·주간(토)·월간(매월 1일)
cd /home/opentest/server/tele
source venv/bin/activate
mkdir -p logs
exec >> "logs/top_signals_$(date +%Y%m%d).log" 2>&1
echo "==== $(date '+%Y-%m-%d %H:%M:%S') start ===="
python top_signals.py
echo "==== $(date '+%Y-%m-%d %H:%M:%S') end ===="
