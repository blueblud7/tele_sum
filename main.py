from datetime import datetime
import telegram_client as tc
import summarizer
import config


def print_report(summaries: dict[str, str], hours_back: int):
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    print("\n" + "=" * 60)
    print(f"텔레그램 요약 리포트  |  최근 {hours_back}시간  |  {now}")
    print("=" * 60)
    for name, summary in summaries.items():
        print(f"\n📢 {name}")
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


async def run():
    await tc.client.start(phone=config.TELEGRAM_PHONE)
    print(f"로그인 완료\n메시지 수집 중 (최근 {config.HOURS_BACK}시간)...")

    channel_data = await tc.collect_all(config.HOURS_BACK)

    if not channel_data:
        print("요약할 메시지가 없습니다.")
        return

    print(f"\n{len(channel_data)}개 채널 요약 중...")
    summaries = summarizer.summarize_all(channel_data)

    print_report(summaries, config.HOURS_BACK)
    save_report(summaries, config.HOURS_BACK)


if __name__ == "__main__":
    import asyncio

    async def main():
        await tc.client.start(phone=config.TELEGRAM_PHONE)
        await run()
        await tc.client.disconnect()

    asyncio.run(main())
