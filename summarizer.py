import json
import re
from concurrent.futures import ThreadPoolExecutor
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


def _safe_summarize(name: str, messages: list[dict]) -> dict:
    try:
        return summarize_channel(name, messages)
    except Exception as e:  # 한 채널 실패가 전체 회차를 막지 않도록
        print(f"  [!] {name} 요약 실패(스킵): {e}")
        return {"summary": "", "important_ids": [], "links": []}


def summarize_all(channel_data: dict[str, list[dict]]) -> dict[str, dict]:
    """채널별 요약을 스레드풀로 동시 실행. 결과는 원본 채널 순서로 반환."""
    items = list(channel_data.items())
    total = len(items)
    if not items:
        return {}
    workers = max(1, min(config.SUMMARY_WORKERS, total))
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futures = [ex.submit(_safe_summarize, name, msgs) for name, msgs in items]

    results = {}
    for i, ((name, _), fut) in enumerate(zip(items, futures), 1):
        r = fut.result()
        s = r.get("summary", "").strip()
        preview = (s[:120] + "…") if len(s) > 120 else (s or "(시그널 없음)")
        print(f"  [{i}/{total}] {name} → {preview}")
        results[name] = r
    return results


META_PROMPT = """입력은 (1) 여러 출처의 마켓 시그널 메모들과 (2) 참조 가능한 링크 목록이다.
이를 주제별로 묶은 뉴스레터형 다이제스트로 재작성하라.

JSON 응답:
{"topics": [
  {"name": "주제명", "items": [
    {"headline": "한 문장 핵심 사실",
     "context": "왜 중요한지 / 배경 한 문장",
     "ref": 링크목록의 번호 또는 null}
  ]}
]}

규칙:
- 주제는 자연스럽게 분류 (예: 글로벌 매크로, 반도체, 한국 종목, 바이오 등).
- 같은 이슈는 한 번만 (중복 제거). 같은 종목·이슈가 여러 항목/주제에 흩어지면 하나로 통합.
- 의미가 거의 동일한 항목은 하나로 합침 (사실·숫자는 보존).
- 사실상 같은 주제가 두 개 이상 헤더로 나뉘면 한 헤더로 통합.
- headline: 한 문장. 사실·숫자·종목명 중심. 채널명·매체명·출처명 금지.
- context: 한 문장. 의미·배경·파급. 메타 표현 금지("요약하면","주요 이슈는","다음과 같다" 등).
- ref: headline의 내용과 일치하는 링크가 목록에 있으면 반드시 그 번호를 넣어라(주제·종목·사건이 같으면 매칭). 일치하는 링크가 없을 때만 null. 없는 번호 합성 금지.
- 중요도 높은 항목 위주. 분량 채우지 말 것. 시그널 없으면 {"topics": []}.
- 작업 과정·지시문을 본문에 옮기지 말 것. 결과 콘텐츠만.
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


def _normalize_topics(topics_raw, link_catalog: list[tuple[str, str]]) -> list[dict]:
    """LLM 출력 topics를 정규화. ref(번호) 또는 url(문자열) 모두 링크 목록으로 검증."""
    url_by_idx = {i: url for i, (_, url) in enumerate(link_catalog)}
    whitelist = set(url_by_idx.values())

    out = []           # 순서 보존된 topic dict 리스트
    by_name = {}        # 정규화 이름 → topic dict (동명 토픽 병합)
    seen_headlines = set()  # 전역 중복 headline 제거
    if not isinstance(topics_raw, list):
        return out
    for t in topics_raw:
        if not isinstance(t, dict):
            continue
        name = str(t.get("name", "")).strip()
        if not name:
            continue
        key = re.sub(r"\s+", "", name.lower())
        topic = by_name.get(key)
        if topic is None:
            topic = {"name": name, "items": []}
            by_name[key] = topic
            out.append(topic)
        items = topic["items"]
        for it in t.get("items", []):
            if not isinstance(it, dict):
                continue
            headline = _strip_urls(str(it.get("headline", "")).strip())
            if not headline:
                continue
            h_key = re.sub(r"\s+", "", headline.lower())
            if h_key in seen_headlines:
                continue
            seen_headlines.add(h_key)
            context = _strip_urls(str(it.get("context", "")).strip())

            url = None
            ref = it.get("ref")
            if isinstance(ref, list):
                ref = ref[0] if ref else None
            if isinstance(ref, (int, str)) and str(ref).strip().lstrip("-").isdigit():
                url = url_by_idx.get(int(ref))
            if url is None:
                u = str(it.get("url", "")).strip()
                if u in whitelist:
                    url = u

            items.append({
                "headline": headline[:140],
                "context": context[:180],
                "url": url,
            })
    return [t for t in out if t["items"]]


def _llm_topics(system_prompt: str, user_content: str,
                link_catalog: list[tuple[str, str]], effort: str = "low") -> list[dict]:
    response = openai_client.chat.completions.create(
        model=config.OPENAI_MODEL,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content},
        ],
        max_completion_tokens=4000,
        reasoning_effort=effort,
        response_format={"type": "json_object"},
    )
    raw = (response.choices[0].message.content or "").strip()
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return []
    return _normalize_topics(data.get("topics", []), link_catalog)


def _format_catalog(link_catalog: list[tuple[str, str]]) -> str:
    if not link_catalog:
        return "(없음)"
    return "\n".join(f"[{i}] {title} | {url}" for i, (title, url) in enumerate(link_catalog))


def aggregate_digest(per_channel: dict[str, dict],
                     link_catalog: list[tuple[str, str]]) -> list[dict]:
    parts = []
    for r in per_channel.values():
        s = r.get("summary", "").strip()
        if s:
            parts.append(s)
    if not parts:
        return []
    combined = "\n\n---\n\n".join(parts)
    user = f"시그널 메모:\n{combined}\n\n참조 가능한 링크:\n{_format_catalog(link_catalog)}"
    return _llm_topics(META_PROMPT, user, link_catalog)


HIGHLIGHT_PROMPT = """입력은 게시될 마켓 다이제스트(주제+항목) JSON이다.
구독자가 3초 안에 핵심을 파악하도록 상단 요약을 만들어라.

JSON 응답:
{"tldr": ["가장 중요한 항목 한 줄", ...최대 3개],
 "tags": ["종목명 또는 핵심 테마", ...최대 5개]}

규칙:
- tldr: 다이제스트에서 시장 영향이 가장 큰 3개를 골라 각각 한 줄(35자 이내). 숫자·종목명 포함. 새 사실 창작 금지.
- tags: 다이제스트에 실제 등장한 종목명/테마 키워드. 공백 없는 단일 토큰(예: 삼성전자, SK하이닉스, 반도체, 금리). 해시태그용이라 공백·특수문자 금지.
- 빈약하면 가능한 만큼만. 시그널 없으면 {"tldr":[],"tags":[]}.
"""


def highlights(topics: list[dict]) -> dict:
    """다이제스트 상단용 핵심 3줄(tldr) + 해시태그(tags) 생성. 실패해도 게시를 막지 않음."""
    if not topics:
        return {"tldr": [], "tags": []}
    draft = json.dumps({"topics": topics}, ensure_ascii=False)
    try:
        resp = openai_client.chat.completions.create(
            model=config.OPENAI_MODEL,
            messages=[
                {"role": "system", "content": HIGHLIGHT_PROMPT},
                {"role": "user", "content": draft},
            ],
            max_completion_tokens=2000,  # gpt-5-nano 추론 토큰 여유 (작으면 출력 0)
            reasoning_effort="low",
            response_format={"type": "json_object"},
        )
        data = json.loads((resp.choices[0].message.content or "").strip())
    except Exception:
        return {"tldr": [], "tags": []}
    tldr = [str(x).strip() for x in data.get("tldr", []) if str(x).strip()][:3]
    tags = []
    for x in data.get("tags", []):
        t = re.sub(r"[^0-9A-Za-z가-힣]", "", str(x))  # 해시태그 안전화 (공백·특수문자 제거)
        if t and t not in tags:
            tags.append(t)
    return {"tldr": tldr, "tags": tags[:5]}


def render_digest(topics: list[dict]) -> str:
    """정규화된 topics를 게시용 텍스트로 렌더링."""
    blocks = []
    for t in topics:
        name = t["name"]
        lines = [f"{_topic_emoji(name)} {name}"]
        for it in t["items"]:
            lines.append("")
            lines.append(f"• {it['headline']}")
            if it.get("context"):
                lines.append(f"  {it['context']}")
            if it.get("url"):
                lines.append(f"  {it['url']}")
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)
