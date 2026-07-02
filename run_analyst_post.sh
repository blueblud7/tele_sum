#!/usr/bin/env bash
# 워치리스트 종목 애널리스트 브리프 생성 + 채널 발행 (KST 주2회: 화·목 08:20)
# DB 적재용 일일 생성(run_analyst_brief.sh, 평일 08:10)과 별개로, 채널에 내보낸다.
cd /home/opentest/server/tele
source venv/bin/activate
mkdir -p logs
exec >> "logs/analyst_post_$(date +%Y%m%d).log" 2>&1
echo "==== $(date '+%Y-%m-%d %H:%M:%S') start ===="
python analyst_brief.py --post
echo "==== $(date '+%Y-%m-%d %H:%M:%S') end ===="
