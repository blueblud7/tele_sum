import json
import re
from openai import OpenAI
import config

openai_client = OpenAI(api_key=config.OPENAI_API_KEY)

URL_RE = re.compile(r"https?://\S+|t\.me/\S+|www\.\S+", re.IGNORECASE)

SYSTEM_PROMPT = f"""금융 시그널을 추출하라.

시그널: 시장·종목·지수에 실제로 영향을 주는 사실
- 실적, M&A, 정책·규제, 경제지표, 기관 리포트, 가격·수급 변동, 단독·독점 정보

노이즈는 무시 (광고, 잡담, 의견, 시세 단순 멘션, 반복 헤드라인).

JSON 응답:
{{
  "summary": "시그널의 핵심 사실만 bullet로. '- '로 시작하는 줄 3~7개.",
  "important_ids": [원본 포워딩 가치가 가장 큰 메시지 ID, 최대 {config.MAX_FORWARDS_PER_CHANNEL}개],
  "links": [{{"title": "헤드라인", "url": "URL"}}]
}}

summary 규칙:
- 한 줄 bullet, 80자 이내. 사실·숫자·종목명만.
- "신호:", "요약:", "정리 필요" 같은 메타 표현 금지.
- 채널명·매체명 금지.
- URL 금지.
- 시그널 없으면 ""

links 규칙:
- 뉴스/리서치 URL만 (광고·소셜·t.me 자기참조 제외)
- 메시지 본문에 실제로 있는 URL만. 변형·합성 금지.
- 최대 5개.
"""


def _strip_urls(text: str) -> str:
    return URL_RE.sub("", text).strip()


def summarize_channel(channel_name: str, messages: list[dict]) -> dict:
    text_block = "\n".join(
        f"[id={m['id']}] [{m['date']}]{' [media]' if m.get('has_media') else ''} {m['text']}"
        for m in messages
    )
    prompt = f"채널명: {channel_name}\n\n{text_block}"

    response = openai_client.chat.completions.create(
        model=config.OPENAI_MODEL,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ],
        max_completion_tokens=1500,
        reasoning_effort="minimal",
        response_format={"type": "json_object"},
    )
    raw = (response.choices[0].message.content or "").strip()
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return {"summary": _strip_urls(raw), "important_ids": [], "links": []}

    s = data.get("summary", "")
    if isinstance(s, list):
        summary = "\n".join(
            (item if item.startswith("-") else f"- {item}").strip()
            for item in (str(x) for x in s)
            if item.strip()
        )
    else:
        summary = str(s).strip()
    summary = _strip_urls(summary)

    valid_ids = {m["id"] for m in messages}
    important_ids = [
        int(x) for x in data.get("important_ids", [])
        if isinstance(x, (int, str)) and str(x).isdigit()
    ]
    important_ids = [i for i in important_ids if i in valid_ids][: config.MAX_FORWARDS_PER_CHANNEL]

    all_text = "\n".join(m.get("text", "") for m in messages)
    valid_links = []
    seen_urls = set()
    for link in data.get("links", []):
        if not isinstance(link, dict):
            continue
        url = str(link.get("url", "")).strip()
        title = str(link.get("title", "")).strip() or "(제목 없음)"
        if not url or url in seen_urls:
            continue
        if url not in all_text:
            continue
        if "t.me/" in url and "/c/" not in url:
            continue
        seen_urls.add(url)
        valid_links.append({"title": title[:80], "url": url})
        if len(valid_links) >= 5:
            break

    return {"summary": summary, "important_ids": important_ids, "links": valid_links}


def summarize_all(channel_data: dict[str, list[dict]]) -> dict[str, dict]:
    results = {}
    total = len(channel_data)
    for i, (name, messages) in enumerate(channel_data.items(), 1):
        r = summarize_channel(name, messages)
        s = r.get("summary", "").strip()
        preview = (s[:120] + "…") if len(s) > 120 else (s or "(시그널 없음)")
        print(f"  [{i}/{total}] {name} → {preview}")
        results[name] = r
    return results


META_PROMPT = """입력은 여러 출처의 마켓 시그널 메모들이다. 이를 하나의 통합 다이제스트로 다시 써라.

JSON 응답:
{"digest": "..."}

digest 형식:
- "▸ 주제명" 헤더로 그룹핑, 그 아래 "- " bullet 나열
- 주제는 자연스럽게 분류 (예: 글로벌 매크로, 반도체, 한국 종목, 바이오 등)

엄수 규칙:
- 한 줄 bullet, 80자 이내. 사실·숫자·종목명만.
- 같은 이슈는 한 번만 (중복 제거).
- 채널명·매체명·출처명 절대 금지.
- 메타 표현 절대 금지: "정리 필요", "반영", "확인", "주요 이슈는", "요약하면", "다음과 같다" 등.
- 작업 과정·지시문을 본문에 옮기지 말 것. 결과 콘텐츠만.
- 분량 채우지 말 것. 시그널 없으면 digest=""
"""


TOPIC_EMOJI = [
    (["글로벌 매크로", "매크로", "거시"], "🌍"),
    (["반도체", "AI 하드웨어", "AI 인프라"], "💻"),
    (["인공지능", " AI", "소프트웨어", "테크"], "🤖"),
    (["바이오", "제약", "헬스케어", "의료"], "💊"),
    (["에너지", "원자재", "유가", "원유", "광물"], "⚡"),
    (["암호화폐", "크립토", "코인", "비트코인", "스테이블"], "🪙"),
    (["부동산", "리츠"], "🏠"),
    (["소비", "리테일", "유통"], "🛒"),
    (["자동차", "전기차", "EV", "모빌리티"], "🚗"),
    (["조선"], "🚢"),
    (["건설"], "🏗️"),
    (["금융", "은행", "증권"], "🏦"),
    (["방산", "국방"], "🛡️"),
    (["엔터", "콘텐츠", "미디어"], "🎬"),
    (["미국"], "🇺🇸"),
    (["중국"], "🇨🇳"),
    (["한국"], "🇰🇷"),
    (["일본"], "🇯🇵"),
    (["유럽"], "🇪🇺"),
    (["정치", "외교", "지정학", "전쟁"], "🏛️"),
    (["환율", "외환"], "💱"),
    (["채권", "금리"], "💵"),
    (["시장", "증시", "주가"], "📈"),
    (["기업", "실적", "산업", "섹터"], "🏢"),
]
DEFAULT_TOPIC_EMOJI = "📌"


def _topic_emoji(name: str) -> str:
    n = name
    for keywords, emoji in TOPIC_EMOJI:
        for kw in keywords:
            if kw in n:
                return emoji
    return DEFAULT_TOPIC_EMOJI


def _clean_digest_string(digest_raw) -> str:
    if isinstance(digest_raw, list):
        digest = "\n".join(str(x).strip() for x in digest_raw if str(x).strip())
    else:
        digest = str(digest_raw).strip()

    items = []  # (kind, text): "topic" | "bullet" | "blank"
    for line in digest.splitlines():
        s = line.rstrip()
        while s.startswith("- ") or s.startswith("• "):
            inner = s[2:].lstrip()
            if inner.startswith("▸") or inner.startswith("- ") or inner.startswith("• "):
                s = inner
            else:
                break

        if not s.strip():
            items.append(("blank", ""))
        elif s.startswith("▸"):
            name = s.lstrip("▸").strip()
            if name:
                items.append(("topic", f"{_topic_emoji(name)} {name}"))
        else:
            items.append(("bullet", s))

    out = []
    for kind, text in items:
        if kind == "blank":
            continue
        if kind == "topic" and out and out[-1] != "":
            out.append("")
        out.append(text)

    return _strip_urls("\n".join(out))


def _llm_digest(system_prompt: str, user_content: str) -> str:
    response = openai_client.chat.completions.create(
        model=config.OPENAI_MODEL,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content},
        ],
        max_completion_tokens=2000,
        reasoning_effort="minimal",
        response_format={"type": "json_object"},
    )
    raw = (response.choices[0].message.content or "").strip()
    try:
        data = json.loads(raw)
        return _clean_digest_string(data.get("digest", ""))
    except json.JSONDecodeError:
        return _clean_digest_string(raw)


def aggregate_digest(per_channel: dict[str, dict]) -> str:
    parts = []
    for r in per_channel.values():
        s = r.get("summary", "").strip()
        if s:
            parts.append(s)
    if not parts:
        return ""
    combined = "\n\n---\n\n".join(parts)
    return _llm_digest(META_PROMPT, combined)


REVIEW_PROMPT = """입력은 게시 직전의 마켓 다이제스트 초안이다. 점검·정리하여 최종본을 반환하라.

점검 항목:
1. 같은 종목·이슈가 서로 다른 bullet/주제에 나뉘어 있으면 하나로 통합
2. 의미가 거의 동일한 bullet은 하나로 합침 (사실·숫자 보존)
3. 80자 초과 bullet은 핵심만 남기고 압축
4. 사실상 같은 주제가 두 개 이상 헤더로 나뉘면 한 헤더로 통합
5. 메타 표현 ("정리 필요", "요약하면", "주요 이슈는" 등) 잔존 시 제거
6. 채널명·매체명·출처명 잔존 시 제거
7. 빈 주제(헤더 아래 bullet 0개) 삭제

규칙:
- 형식 유지: "▸ 주제명" 헤더 + 그 아래 "- " bullet
- 새 정보 추가 금지. 정리만.
- 의미 있는 시그널이 사라지면 "" 반환

JSON 응답: {"digest": "..."}
"""


def review_digest(digest: str) -> str:
    if not digest.strip():
        return digest
    return _llm_digest(REVIEW_PROMPT, digest)
