"""선정된 채널을 텔레그램에서 실제 leave (수집 중단은 leave로만 가능).

사용:
  python leave_channels.py           # dry-run: 매칭되는 dialog만 출력
  python leave_channels.py --apply    # 실제 leave + channels.selected=false
"""
import argparse, asyncio
import telegram_client as tc
import config
import db

# 2026-07-02 큐레이션 결정: 명확 DROP 14(+동명 매크로 1) + WATCH→DROP 9 = 24개
TARGET_IDS = {
    # 매크로/뉴스피드·중복·저품질 (명확 DROP)
    2595233360,  # 해외정보 분석
    1263412188,  # Market News Feed
    2583177558,  # Quick Financial News (@quick_engNews)
    2025567712,  # Quick Financial News (@usfinancialnews) - 동명 매크로, 윈도우 무활동
    1223459481,  # WSJ World News
    2506876270,  # 나박 AI 외신속보
    1821932169,  # 회색인간의 매크로
    1564582698,  # 범송공자 X 29PER
    1362297722,  # 한국투자증권 이민근
    1544338145,  # 원리버 Oneriver
    1298416162,  # 미국 제약-바이오 약장수
    1893729962,  # 글로벌바이오아저씨
    1462338131,  # Donald J. Trump
    2350527192,  # 탐방왕
    1550266895,  # iM전략 김준영
    # WATCH -> DROP (저시그널·고노이즈)
    1301601966,  # 호그니엘
    1720065182,  # 기술적 분석 (소라게아빠)
    1589472530,  # YIELD & SPREAD
    1761895544,  # 한화투자증권 경제 임혜윤
    1288659153,  # 매경 월가월부
    2098793993,  # KK Kontemporaries
    1232678782,  # MagazinE.
    1260023521,  # 재야의 고수들
    2471352838,  # 미국 주식 인사이더
}


async def main(apply: bool):
    await tc.client.start(phone=config.TELEGRAM_PHONE)
    dialogs = await tc.get_all_channels()
    matched = [d for d in dialogs if abs(int(d.entity.id)) in TARGET_IDS]
    found_ids = {abs(int(d.entity.id)) for d in matched}
    missing = TARGET_IDS - found_ids

    print(f"매칭 {len(matched)}/{len(TARGET_IDS)}개:")
    for d in matched:
        print(f"  - {abs(int(d.entity.id))}  {d.name}")
    if missing:
        print(f"\n미발견(이미 나갔거나 세션에 없음) {len(missing)}개: {sorted(missing)}")

    if not apply:
        print("\n[dry-run] 실제 leave하려면 --apply")
        await tc.client.disconnect()
        return

    print("\nleave 실행...")
    left = []
    for d in matched:
        try:
            await tc.client.delete_dialog(d.entity)
            left.append(abs(int(d.entity.id)))
            print(f"  ✓ left {d.name}")
        except Exception as e:
            print(f"  ✗ 실패 {d.name}: {e}")

    if left:
        with db.connection() as c:
            c.execute("update channels set selected=false, updated_at=now() where id = any(%s)", (left,))
        print(f"\nDB selected=false 반영 {len(left)}개")
    await tc.client.disconnect()
    db.close()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()
    asyncio.run(main(a.apply))
