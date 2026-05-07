import asyncio
import argparse
from datetime import datetime
import telegram_client as tc
import channel_selector as cs
import summarizer
import config


def print_report(summaries: dict[str, str], hours_back: int):
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    print("\n" + "=" * 60)
    print(f"텔레그램 요약 리포트  |  최근 {hours_back}시간  |  {now}")
    print("=" * 60)
    for name, summary in summaries.items():
        print(f"\n[{name}]")
        print("-" * 40)
        print(summary)
    print("\n" + "=" * 60)


def save_report(summaries: dict[str, str], hours_back: int):
    now = datetime.now().strftime("%Y%m%d_%H%M")
    filename = f"report_{now}.txt"
    with open(filename, "w", encoding="utf-8") as f:
        f.write(f"텔레그램 요약 리포트 | 최근 {hours_back}시간 | {now}\n")
        f.write("=" * 60 + "\n")
        for name, summary in summaries.items():
            f.write(f"\n[{name}]\n{summary}\n")
    print(f"\n리포트 저장: {filename}")


async def main(reselect: bool):
    await tc.client.start(phone=config.TELEGRAM_PHONE)
    print("로그인 완료")

    all_dialogs = await tc.get_all_channels()

    saved = cs.load_selection()
    if saved is None or reselect:
        selected_dialogs = cs.select_channels(all_dialogs)
        saved_ids = [d.entity.id for d in selected_dialogs]
        cs.save_selection(saved_ids)
        print(f"\n선택 저장 완료 (다음 실행부터 자동 적용, --reselect 로 변경 가능)")
    else:
        id_set = set(saved)
        selected_dialogs = [d for d in all_dialogs if d.entity.id in id_set]
        names = [d.name for d in selected_dialogs]
        print(f"저장된 채널 {len(selected_dialogs)}개 사용: {', '.join(names)}")
        print("(채널 변경: --reselect 옵션 사용)")

    print(f"\n메시지 수집 중 (최근 {config.HOURS_BACK}시간)...")
    channel_data = await tc.collect_from(selected_dialogs, config.HOURS_BACK)

    if not channel_data:
        print("요약할 메시지가 없습니다.")
        await tc.client.disconnect()
        return

    print(f"\n{len(channel_data)}개 채널 요약 중...")
    summaries = summarizer.summarize_all(channel_data)

    print_report(summaries, config.HOURS_BACK)
    save_report(summaries, config.HOURS_BACK)

    await tc.client.disconnect()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="텔레그램 채널 요약기")
    parser.add_argument("--reselect", action="store_true", help="채널 재선택")
    args = parser.parse_args()

    asyncio.run(main(reselect=args.reselect))
