import asyncio
import argparse
from datetime import datetime
import telegram_client as tc
import channel_selector as cs
import summarizer
import bot_poster
import state
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


async def main(reselect: bool):
    await tc.client.start(phone=config.TELEGRAM_PHONE)

    all_dialogs = await tc.get_all_channels()

    saved = cs.load_selection()
    if saved is None or reselect:
        selected_dialogs = cs.select_channels(all_dialogs)
        cs.save_selection([d.entity.id for d in selected_dialogs])
        print(f"\n선택 저장 완료")
    else:
        id_set = set(saved)
        selected_dialogs = [d for d in all_dialogs if d.entity.id in id_set]
        print(f"채널 {len(selected_dialogs)}개 모니터링")

    last_seen = state.load()
    print(f"\n신규 메시지 수집 (lookback {config.LOOKBACK_MINUTES}분)...")
    channel_data, max_ids, dialog_by_name = await tc.collect_new(
        selected_dialogs, last_seen, config.LOOKBACK_MINUTES
    )

    if not channel_data:
        print("신규 요약 대상 없음")
        if max_ids:
            last_seen.update(max_ids)
            state.save(last_seen)
        await tc.client.disconnect()
        return

    print(f"\n{len(channel_data)}개 채널 요약 중...")
    results = summarizer.summarize_all(channel_data)

    print("\n주제별 통합 다이제스트 생성 중...")
    digest = summarizer.aggregate_digest(results)

    if digest:
        print("게시 전 리뷰·정리 중...")
        digest = summarizer.review_digest(digest)

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
    parser = argparse.ArgumentParser(description="텔레그램 채널 요약기")
    parser.add_argument("--reselect", action="store_true", help="채널 재선택")
    args = parser.parse_args()
    asyncio.run(main(reselect=args.reselect))
