"""Scheduler — enqueue new videos with per-platform offsets, send due posts.

Time model: all timestamps are local (Asia/Shanghai) naive ISO strings,
so SQLite string comparison behaves correctly.
"""

import re
import sys
from datetime import datetime, timedelta

import pytz

from ..collectors.youtube import CollectedItem
from ..publishers import build_publisher, enabled_platforms
from ..storage import Storage

_TG_TITLE = "telegram_title"
_TG_BODY = "telegram_body"


def local_now(config: dict) -> datetime:
    tz_name = config.get("app", {}).get("timezone", "Asia/Shanghai")
    return datetime.now(pytz.timezone(tz_name))


def _iso(dt: datetime) -> str:
    return dt.isoformat(timespec="seconds")


def _compose_telegram(copy: dict, video_url: str = "") -> str:
    """Compose the final Telegram message from title + body.

    Guards against the AI repeating the title inside the body (the first
    line is dropped when it looks like a duplicate title), and ensures
    the YouTube video link is present at the end of the message.
    """
    title = (copy.get(_TG_TITLE) or "").strip()
    body = (copy.get(_TG_BODY) or "").strip()

    # 防御：body 首行若近似等于标题（可能带【】或 # 等包裹），剔除
    def _norm(s: str) -> str:
        return re.sub(r"[【】#*《》\s：:]", "", s)

    lines = body.split("\n")
    first = lines[0].strip()
    if title and body and _norm(first) == _norm(title):
        lines = lines[1:]
    body = "\n".join(lines).strip()

    # 确保末尾带上 YouTube 视频链接（只出现一次）
    if video_url and video_url not in body:
        body = f"{body}\n\n🔗 视频：{video_url}" if body else video_url

    return f"{title}\n\n{body}" if title else body


def enqueue_video(storage: Storage, video: CollectedItem, copy: dict,
                  config: dict, thumb_path: str | None) -> int:
    """Create a scheduled post for each enabled platform.

    Skips platforms that already have a post for this video (idempotent).
    Returns the number of posts created.
    """
    publish_cfg = config.get("publish", {})
    offsets = publish_cfg.get("offsets", {})
    now = local_now(config)
    count = 0
    for platform in enabled_platforms(config):
        if storage.has_post(video.item_id, platform):
            continue
        if platform == "telegram":
            content = _compose_telegram(copy, video.url)
        else:
            content = copy.get(platform, "").strip()
        if not content:
            print(f"  [Scheduler] Skip {platform}: empty copy")
            continue
        send_at = now + timedelta(minutes=int(offsets.get(platform, 0)))
        storage.create_post(
            video_id=video.item_id,
            video_title=video.title,
            video_url=video.url,
            platform=platform,
            content=content,
            thumb_path=thumb_path,
            send_at=_iso(send_at),
        )
        count += 1
        print(f"  [Scheduler] Queued {platform} -> {send_at.strftime('%m-%d %H:%M')}")
    return count


def send_due(storage: Storage, config: dict, sheets=None) -> int:
    """Publish every queued post whose send_at has arrived.

    On failure the post is retried up to ``max_retries`` total attempts
    (2 = 首发 + 只重试 1 次) with a ``retry_delay_minutes`` interval,
    then marked failed.  Every attempt is recorded in SQLite publish_log
    and (if configured) Google Sheets.
    Returns the number of due posts processed.
    """
    publish_cfg = config.get("publish", {})
    max_retries = int(publish_cfg.get("max_retries", 3))
    retry_delay = int(publish_cfg.get("retry_delay_minutes", 30))

    now = local_now(config)
    due = storage.due_posts(_iso(now))
    if not due:
        print("  [Scheduler] No due posts")
        return 0

    processed = 0
    for post in due:
        platform = post["platform"]
        now = local_now(config)
        now_s = _iso(now)
        # 最后防线：该视频+平台若已有成功发布记录（例如缓存不一致/补发
        # 逻辑误入队造成重复），直接标记完成，绝不重复发送。
        if storage.has_success(post["video_id"], platform):
            print(f"  [Scheduler] Skip {platform}: already published, mark done")
            storage.mark_published(post["id"], now_s)
            processed += 1
            continue
        publisher = build_publisher(platform, config)
        if publisher is None:
            print(f"  [Scheduler] Publisher '{platform}' not available, skip")
            continue

        result = publisher.publish(post)
        processed += 1

        if result.success:
            storage.mark_published(post["id"], now_s)
            storage.add_log(post["id"], post["video_id"], post["video_title"],
                            post["video_url"], platform, "success",
                            result.url or result.message)
            print(f"  [Scheduler] ✓ {platform} published: {result.message}")
        else:
            retries = post["retry_count"] + 1
            final = retries >= max_retries
            new_send_at = (
                _iso(now + timedelta(minutes=retry_delay)) if not final else None
            )
            storage.mark_failed(post["id"], result.message, retries,
                                new_send_at, final)
            storage.add_log(post["id"], post["video_id"], post["video_title"],
                            post["video_url"], platform, "failed",
                            result.message)
            print(f"  [Scheduler] ✗ {platform} failed"
                  f" (retry {retries}/{max_retries}): {result.message[:120]}")

        # 同步 Google Sheets 记录（成功/失败都写）
        if sheets is not None:
            sheets.log_publish(
                video_id=post["video_id"],
                video_title=post["video_title"],
                video_url=post["video_url"],
                platform=platform,
                status="success" if result.success else "failed",
                message=result.url or result.message,
                content_preview=post["content"][:120],
            )

    return processed
