"""
YouTube 자막 추출 검증용 CLI.

사용법:
  python yt_transcript_test.py <URL_or_video_id> [<URL_or_video_id> ...]
  python yt_transcript_test.py https://youtu.be/iCvmsMzlF7o
  python yt_transcript_test.py iCvmsMzlF7o
"""

import re
import sys
from urllib.parse import parse_qs, urlparse

from youtube_transcript_api import YouTubeTranscriptApi


def extract_video_id(s: str) -> str | None:
    if re.fullmatch(r"[A-Za-z0-9_-]{11}", s):
        return s
    m = re.search(
        r"(?:youtube\.com/watch\?.*v=|youtu\.be/|youtube\.com/embed/|youtube\.com/shorts/)([A-Za-z0-9_-]{11})",
        s,
    )
    if m:
        return m.group(1)
    qs = parse_qs(urlparse(s).query)
    return qs["v"][0] if "v" in qs else None


def probe(target: str, langs: list[str] = ("ko", "en", "en-US", "en-GB")) -> None:
    vid = extract_video_id(target)
    print(f"\n── 입력: {target}")
    if not vid:
        print("  ✗ video_id 추출 실패")
        return
    print(f"  video_id: {vid}")

    ytt = YouTubeTranscriptApi()
    try:
        tlist = ytt.list(vid)
        avail = [(t.language_code, "auto" if t.is_generated else "manual") for t in tlist]
        print(f"  사용 가능 자막 ({len(avail)}개): {avail[:10]}")
    except Exception as e:
        print(f"  ✗ list 실패: {type(e).__name__}: {e}")
        return

    try:
        fetched = ytt.fetch(vid, languages=list(langs))
        text = " ".join(s.text for s in fetched)
        print(f"  ✓ 추출 성공: lang={fetched.language_code}, segs={len(fetched)}, chars={len(text)}")
        preview = text[:200].replace("\n", " ")
        print(f"  앞 200자: {preview}")
    except Exception as e:
        print(f"  ✗ fetch 실패: {type(e).__name__}: {e}")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    for arg in sys.argv[1:]:
        probe(arg)
