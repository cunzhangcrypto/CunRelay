"""YouTube channel collector.

  - Discovery via YouTube official RSS feeds (no API key needed)
  - Transcript via youtube-transcript-api (no API key needed)
  - Thumbnail download from img.youtube.com with fallback sizes
"""

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import feedparser
import requests


@dataclass
class CollectedItem:
    """A video discovered from a YouTube channel."""
    source: str
    source_name: str
    item_id: str
    title: str
    url: str
    published: datetime | None
    description: str = ""
    channel_id: str = ""
    transcript: str = ""
    thumb_path: str | None = None
    extra: dict = field(default_factory=dict)


def _resolve_handle(handle: str) -> str | None:
    """Resolve a YouTube @handle to a channel_id by scraping the channel page."""
    handle = handle.lstrip("@")
    url = f"https://www.youtube.com/@{handle}"
    try:
        resp = requests.get(
            url,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/120.0.0.0 Safari/537.36"
                ),
                "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            },
            timeout=15,
        )
        resp.raise_for_status()
    except Exception as e:
        print(f"  [YouTube] Failed to resolve handle @{handle}: {e}")
        return None

    m = re.search(r'"channelId"\s*:\s*"((UC|UU)[a-zA-Z0-9_-]+)"', resp.text)
    if m:
        return m.group(1)
    m = re.search(r'"externalId"\s*:\s*"((UC|UU)[a-zA-Z0-9_-]+)"', resp.text)
    if m:
        return m.group(1)
    print(f"  [YouTube] Could not extract channel_id from @{handle} page")
    return None


def fetch_transcript(video_id: str, max_chars: int = 6000) -> str:
    """Fetch a video transcript, returning plain text (truncated).

    Lists all available subtitles and picks the best by priority:
    zh-CN > zh-Hans > zh > en, manual over auto-generated within the
    same language.  Returns "" on failure (caller degrades gracefully).
    """
    try:
        from youtube_transcript_api import YouTubeTranscriptApi

        api = YouTubeTranscriptApi()
        transcripts = api.list(video_id)

        priority = {"zh-cn": 0, "zh-hans": 1, "zh": 2, "en": 3}
        scored = []
        for t in transcripts:
            code = t.language_code
            lang_score = priority.get(code.lower(), 99)
            # 同语言内：手动字幕优先于自动生成（auto 加 10 分靠后）
            if getattr(t, "is_generated", False):
                lang_score += 10
            scored.append((lang_score, code))
        if not scored:
            return ""

        scored.sort()
        best = scored[0][1]
        fetched = api.fetch(video_id, languages=[best])
        text = " ".join(snippet.text for snippet in fetched)
        return text[:max_chars] if text else ""
    except ImportError:
        print("  [YouTube] youtube-transcript-api not installed")
    except Exception as e:
        print(f"  [YouTube] Transcript failed for {video_id}: {e}")
    return ""


def download_thumbnail(video_id: str, out_dir: str) -> str | None:
    """Download the best available thumbnail for a video.

    Tries maxresdefault -> hqdefault.  Returns the local file path.
    """
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    for name in ("maxresdefault", "hqdefault"):
        dest = out / f"{video_id}_{name}.jpg"
        if dest.exists():
            return str(dest)
        url = f"https://img.youtube.com/vi/{video_id}/{name}.jpg"
        try:
            resp = requests.get(url, timeout=20)
            if resp.status_code == 200 and len(resp.content) > 2000:
                dest.write_bytes(resp.content)
                print(f"  [YouTube] Thumbnail saved: {dest.name} ({len(resp.content)} bytes)")
                return str(dest)
        except Exception as e:
            print(f"  [YouTube] Thumbnail failed ({name}): {e}")
    return None


class YouTubeCollector:
    """Collect latest videos from a list of YouTube channels."""

    def __init__(self, channels: list[dict]) -> None:
        self.channels = channels

    def collect(self) -> list[CollectedItem]:
        items: list[CollectedItem] = []
        for ch in self.channels:
            name = ch["name"]
            channel_id = ch.get("channel_id")
            if not channel_id:
                handle = ch.get("handle", name)
                print(f"  [YouTube] Resolving handle @{handle.lstrip('@')} for '{name}'...")
                channel_id = _resolve_handle(handle)
                if not channel_id:
                    print(f"  [YouTube] Skipping '{name}': could not resolve handle")
                    continue

            feed_url = f"https://www.youtube.com/feeds/videos.xml?channel_id={channel_id}"
            try:
                feed = feedparser.parse(feed_url)
            except Exception as e:
                print(f"  [YouTube] Failed to fetch '{name}': {e}")
                continue

            if feed.bozo and not feed.entries:
                print(f"  [YouTube] No entries for '{name}' (bad feed)")
                continue

            for entry in feed.entries:
                video_id = entry.get("yt_videoid", entry.id)
                published = None
                if "published_parsed" in entry and entry.published_parsed:
                    published = datetime(*entry.published_parsed[:6], tzinfo=timezone.utc)
                items.append(CollectedItem(
                    source="youtube",
                    source_name=name,
                    item_id=f"yt:{video_id}",
                    title=entry.get("title", "(no title)"),
                    url=f"https://www.youtube.com/watch?v={video_id}",
                    published=published,
                    description=entry.get("summary", ""),
                    channel_id=channel_id,
                ))
            print(f"  [YouTube] '{name}': {len(feed.entries)} videos found")

        return items
