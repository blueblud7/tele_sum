from openai import OpenAI
import config

openai_client = OpenAI(api_key=config.OPENAI_API_KEY)

SYSTEM_PROMPT = f"""당신은 텔레그램 채널 메시지를 요약하는 어시스턴트입니다.
주어진 메시지들을 {config.LANGUAGE}로 간결하게 요약해 주세요.

요약 형식:
- 핵심 주제/이슈를 bullet point로 3~7개 정리
- 중요한 링크, 숫자, 날짜가 있으면 포함
- 중복되거나 사소한 내용은 생략
- 전체 요약은 300자 이내"""


def summarize_channel(channel_name: str, messages: list[dict]) -> str:
    text_block = "\n".join(
        f"[{m['date']}] {m['text']}" for m in messages
    )
    prompt = f"채널명: {channel_name}\n\n{text_block}"

    response = openai_client.chat.completions.create(
        model=config.OPENAI_MODEL,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ],
        max_tokens=500,
        temperature=0.3,
    )
    return response.choices[0].message.content.strip()


def summarize_all(channel_data: dict[str, list[dict]]) -> dict[str, str]:
    summaries = {}
    total = len(channel_data)
    for i, (name, messages) in enumerate(channel_data.items(), 1):
        print(f"  요약 중 [{i}/{total}] {name}...")
        summaries[name] = summarize_channel(name, messages)
    return summaries
