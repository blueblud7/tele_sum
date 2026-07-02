import asyncio
from datetime import datetime
from zoneinfo import ZoneInfo
import telegram_client as tc
import channel_selector as cs
import summarizer
import bot_poster
import state
import repo
import config


# 하루 4회 슬롯별 정체성 (KST 발송 시각 → 이름). 예측 가능한 리듬을 준다.
SLOT_NAMES = {7: "장전 브리핑", 12: "오전 마감", 16: "장 마감", 21: "미국장 셋업"}


def build_post_text(digest: str, hl: dict | None = None) -> str:
    if not digest:
        return ""
    now = datetime.now(ZoneInfo("Asia/Seoul"))
    slot = SLOT_NAMES.get(now.hour)
    stamp = now.strftime("%m-%d %H:%M")
    head = "📰 시그널 요약"
    if slot:
        head += f" · {slot}"
    head += f" | {stamp}"

    parts = [head]
    if hl and hl.get("tldr"):
        lines = ["🔑 핵심 3줄"] + [f"• {t}" for t in hl["tldr"]]
        parts.append("\n".join(lines))
    parts.append(digest)
    if hl and hl.get("tags"):
        parts.append("🏷 " + " ".join(f"#{t}" for t in hl["tags"]))
    return "\n\n".join(parts)


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

    # 항목에 인라인으로 붙일 링크 카탈로그 (뉴스 URL + 중요 메시지 t.me 링크)
    link_catalog = collect_links(results, channel_data, dialog_by_name, max_total=40)

    print("\n주제별 통합 다이제스트 생성 중...")
    topics = summarizer.aggregate_digest(results, link_catalog)

    digest = summarizer.render_digest(topics)
    hl = summarizer.highlights(topics)

    if digest:
        all_collected = [m for msgs in channel_data.values() for m in msgs]
        start, end = repo.message_window(all_collected)
        repo.save_summary(
            kind="digest",
            content=digest,
            period_start=start,
            period_end=end,
            model=config.OPENAI_MODEL,
            meta={"channels": list(channel_data.keys()), "topics": topics},
        )

    post_text = build_post_text(digest, hl)
    print("\n--- 게시 본문 ---")
    print(post_text or "(시그널 없음 — 게시 스킵)")
    print("--- ---")

    last_seen.update(max_ids)
    state.save(last_seen)

    if post_text:
        print(f"\n봇으로 {config.TARGET_CHANNEL} 게시 중...")
        try:
            ids = bot_poster.post(post_text)
            print("게시 완료")
            # 장전(07시) 브리핑만 핀 고정 → 하루 종일 신규 방문자에게 첫인상으로 노출.
            if datetime.now(ZoneInfo("Asia/Seoul")).hour == 7 and ids:
                try:
                    bot_poster.unpin_all()
                    bot_poster.pin(ids[0])
                    print("장전 브리핑 핀 고정")
                except Exception as e:
                    print(f"핀 고정 실패(무시): {e}")
        except Exception as e:
            print(f"게시 실패 (이번 회차 스킵, 커서는 이미 진행됨): {e}")
    else:
        print("\n게시 스킵 (시그널 없음)")

    await tc.client.disconnect()


if __name__ == "__main__":
    asyncio.run(main())
