#!/usr/bin/env bash
# 채널 성장 지표 수집: 구독자 수 + 게시물별 조회/공유 수 (KST 일 1회)
cd /home/opentest/server/tele
source venv/bin/activate
mkdir -p logs
exec >> "logs/metrics_$(date +%Y%m%d).log" 2>&1
echo "==== $(date '+%Y-%m-%d %H:%M:%S') start ===="
python channel_metrics.py
echo "==== $(date '+%Y-%m-%d %H:%M:%S') end ===="
