import asyncio
from datetime import datetime
import telegram_client as tc
import channel_selector as cs
import summarizer
import bot_poster
import state
import repo
import config


def build_post_text(digest: str, link_entries: list[tuple[str, str]]) -> str:
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    if not digest and not link_entries:
        return ""

    lines = [f"📰 시그널 요약 | {now}"]
    if digest:
        lines.append("")
        lines.append(digest)

    if link_entries:
        lines.append("\n\n🔗 주요 링크")
        for title, url in link_entries:
            lines.append(f"- {title}\n  {url}")

    return "\n".join(lines)


def collect_links(results: dict[str, dict], channel_data: dict[str, list[dict]],
                  dialog_by_name: dict, max_total: int = 10) -> list[tuple[str, str]]:
    seen = set()
    out = []

    # News URLs from per-channel curated links
    for r in results.values():
        for link in r.get("links", []):
            url = link.get("url", "")
            title = link.get("title", "").strip() or "(제목 없음)"
            if not url or url in seen:
                continue
            seen.add(url)
            out.append((title, url))

    # Important messages as t.me links
    for name, r in results.items():
        ids = r.get("important_ids", [])
        if not ids:
            continue
        dialog = dialog_by_name.get(name)
        if dialog is None:
            continue
        msgs_by_id = {m["id"]: m for m in channel_data.get(name, [])}
        for mid in ids:
            url = tc.message_link(dialog, mid)
            if url in seen:
                continue
            seen.add(url)
            msg = msgs_by_id.get(mid, {})
            text = (msg.get("text") or "").strip().replace("\n", " ")
            title = text[:60] if text else "(미디어 메시지)"
            out.append((title, url))

    return out[:max_total]


async def main():
    await tc.client.start(phone=config.TELEGRAM_PHONE)

    all_dialogs = await tc.get_all_channels()
    n_selected, n_unsubscribed = cs.sync_channels(all_dialogs)
    selected_dialogs = all_dialogs
    msg = f"채널 {n_selected}개 모니터링"
    if n_unsubscribed:
        msg += f" (구독 해제 {n_unsubscribed}개)"
    print(msg)

    last_seen = state.load()
    print(f"\n신규 메시지 수집 (lookback {config.LOOKBACK_MINUTES}분)...")
    channel_data, max_ids, dialog_by_name, all_msgs = await tc.collect_new(
        selected_dialogs, last_seen, config.LOOKBACK_MINUTES
    )

    if all_msgs:
        n = repo.save_messages(all_msgs)
        print(f"  → DB에 메시지 {n}건 저장 (중복 무시)")

    if not channel_data:
        print("신규 요약 대상 없음")
        if max_ids:
            last_seen.update(max_ids)
            state.save(last_seen)
        await tc.client.disconnect()
        return

    print(f"\n{len(channel_data)}개 채널 요약 중...")
    results = summarizer.summarize_all(channel_data)

    name_to_channel_id = {d.name: int(d.entity.id) for d in selected_dialogs}
    for name, r in results.items():
        summary_text = (r.get("summary") or "").strip()
        if not summary_text:
            continue
        ch_id = name_to_channel_id.get(name)
        start, end = repo.message_window(channel_data.get(name, []))
        repo.save_summary(
            kind="channel",
            channel_id=ch_id,
            content=summary_text,
            period_start=start,
            period_end=end,
            model=config.OPENAI_MODEL,
            meta={
                "important_ids": r.get("important_ids", []),
                "links": r.get("links", []),
            },
        )

    print("\n주제별 통합 다이제스트 생성 중...")
    digest = summarizer.aggregate_digest(results)

    if digest:
        print("게시 전 리뷰·정리 중...")
        digest = summarizer.review_digest(digest)

    if digest:
        all_collected = [m for msgs in channel_data.values() for m in msgs]
        start, end = repo.message_window(all_collected)
        repo.save_summary(
            kind="digest",
            content=digest,
            period_start=start,
            period_end=end,
            model=config.OPENAI_MODEL,
            meta={"channels": list(channel_data.keys())},
        )

    link_entries = collect_links(results, channel_data, dialog_by_name)
    post_text = build_post_text(digest, link_entries)
    print("\n--- 게시 본문 ---")
    print(post_text or "(시그널 없음 — 게시 스킵)")
    print("--- ---")

    last_seen.update(max_ids)
    state.save(last_seen)

    if post_text:
        print(f"\n봇으로 {config.TARGET_CHANNEL} 게시 중...")
        try:
            bot_poster.post(post_text)
            print("게시 완료")
        except Exception as e:
            print(f"게시 실패 (이번 회차 스킵, 커서는 이미 진행됨): {e}")
    else:
        print("\n게시 스킵 (시그널 없음)")

    await tc.client.disconnect()


if __name__ == "__main__":
    asyncio.run(main())
