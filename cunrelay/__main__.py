"""CunRelay main entry point.

Commands:
  python -m cunrelay collect   发现新视频 → 抓字幕/封面 → AI 生成文案 → 入队
  python -m cunrelay send      发送所有到点的排期
  python -m cunrelay export    导出 public/data.json（线上 UI 数据快照）
  python -m cunrelay serve     本地启动 Web UI
  python -m cunrelay           默认：collect + send + export 一次跑完
"""

import argparse
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .ai import generate_platform_copy
from .collectors.youtube import YouTubeCollector, download_thumbnail, fetch_transcript
from .config import load_config, project_root
from .export import export_records
from .scheduler import enqueue_video, local_now, send_due
from .sheets import SheetsLogger
from .storage import Storage


def _filter_by_age(items, max_hours, now):
    """Keep only videos published within the last ``max_hours``."""
    if max_hours <= 0:
        return items
    cutoff = now - timedelta(hours=max_hours)
    return [it for it in items if it.published and it.published >= cutoff]


def _collect(storage: Storage, config: dict) -> None:
    """Discover new videos, enrich them, generate copy and enqueue."""
    app_cfg = config.get("app", {})
    max_age = int(app_cfg.get("max_item_age_hours", 72))
    output_dir = Path(app_cfg.get("output_dir", "output"))
    thumb_dir = output_dir / "thumbs"

    ai_cfg = config.get("ai", {})
    api_key = ai_cfg.get("api_key")
    if not api_key:
        print("  [AI] Skipped (DEEPSEEK_API_KEY not configured)")

    channels = config.get("follow", {}).get("youtube", [])
    if not channels:
        print("  [Collect] No YouTube channels configured")
        return

    print("\n[Collect] Discovering YouTube videos...")
    now = local_now(config)
    items = YouTubeCollector(channels).collect()
    aged = _filter_by_age(items, max_age, now)
    pending = [it for it in aged if storage.is_new_video(it.item_id)]
    print(f"  [Collect] {len(aged)} videos in time window, {len(pending)} new")

    # 每个频道每轮只处理最新发布的 1 个视频（频道隔天/每天更新）
    by_channel: dict[str, list] = {}
    for it in pending:
        by_channel.setdefault(it.source_name, []).append(it)
    selected: list = []
    for _, vids in by_channel.items():
        vids.sort(
            key=lambda v: v.published or datetime(1970, 1, 1, tzinfo=timezone.utc),
            reverse=True,
        )
        selected.append(vids[0])

    # 未选中的新视频：标记 seen，本轮不处理（防止下轮重复发现）
    selected_ids = {it.item_id for it in selected}
    for it in pending:
        if it.item_id not in selected_ids:
            storage.mark_video_seen(it.item_id, it.source, it.source_name,
                                    it.title, it.url, None)
            print(f"  → 跳过（每轮每频道只发最新1条）: {it.title}")

    for item in selected:
        if not storage.is_new_video(item.item_id):
            continue
        print(f"\n  → New video: {item.title}")

        # 1. transcript
        video_id = item.item_id.split(":", 1)[-1]
        transcript = fetch_transcript(video_id, ai_cfg.get("max_transcript_chars", 6000))
        if transcript:
            print(f"    transcript: {len(transcript)} chars")

        # 2. thumbnail
        thumb = download_thumbnail(video_id, str(thumb_dir))
        if thumb:
            storage.update_thumb(item.item_id, thumb)

        # 3. AI copy (skip video if AI not configured)
        if not api_key:
            storage.mark_video_seen(item.item_id, item.source, item.source_name,
                                    item.title, item.url, thumb)
            print("    (skipped AI copy — no API key)")
            continue

        copy = generate_platform_copy(
            video=item,
            transcript=transcript,
            api_key=api_key,
            model=ai_cfg.get("model", "deepseek-chat"),
            api_base=ai_cfg.get("api_base", "https://api.deepseek.com"),
            timeout=int(ai_cfg.get("timeout", 180)),
            max_transcript_chars=int(ai_cfg.get("max_transcript_chars", 6000)),
        )
        if not copy:
            print(f"    AI copy failed for '{item.title}', marking seen and skipping")
            storage.mark_video_seen(item.item_id, item.source, item.source_name,
                                    item.title, item.url, thumb)
            continue

        # 4. enqueue
        storage.mark_video_seen(item.item_id, item.source, item.source_name,
                                item.title, item.url, thumb)
        created = enqueue_video(storage, item, copy, config, thumb)
        print(f"    enqueued {created} posts")


def _send(storage: Storage, config: dict, sheets: SheetsLogger | None) -> None:
    print("\n[Send] Processing due posts...")
    send_due(storage, config, sheets)


def _serve(config: dict) -> None:
    web_cfg = config.get("web", {})
    host = web_cfg.get("host", "127.0.0.1")
    port = int(web_cfg.get("port", 8080))

    import uvicorn
    from .web.app import create_app

    app = create_app(config)
    print(f"\n  🖥  CunRelay Web UI -> http://{host}:{port}")
    uvicorn.run(app, host=host, port=port, log_level="info")


def main() -> None:
    parser = argparse.ArgumentParser(prog="cunrelay", description="CunRelay 内容自动分发")
    parser.add_argument("command", nargs="?", default="all",
                        choices=["collect", "send", "export", "serve", "all"],
                        help="collect=采集+AI+入队, send=发送到点排期, export=导出UI数据, serve=本地Web UI, all=全流程")
    args = parser.parse_args()

    print("=" * 50)
    print("  🔁 CunRelay - YouTube → AI → 多平台分发")
    print("=" * 50)

    config = load_config(os.environ.get("CONFIG_PATH"))

    if args.command == "serve":
        _serve(config)
        return

    output_dir = Path(config.get("app", {}).get("output_dir", "output"))
    output_dir.mkdir(parents=True, exist_ok=True)
    db_path = str(output_dir / "cunrelay.db")
    storage = Storage(db_path)

    sheets = SheetsLogger(config.get("sheets", {}),
                          config.get("app", {}).get("timezone", "Asia/Shanghai"))

    if args.command in ("all", "collect"):
        _collect(storage, config)
    if args.command in ("all", "send"):
        _send(storage, config, sheets)
    if args.command in ("all", "export"):
        _export(storage, config)

    storage.close()
    print(f"\n{'=' * 50}")
    print("  ✅ Done")
    print(f"{'=' * 50}\n")


def _export(storage: Storage, config: dict) -> None:
    """导出 public/data.json（线上 UI 数据快照）。"""
    print("\n[Export] Generating public/data.json...")
    public_dir = project_root() / "public"
    export_records(storage, config, public_dir)


if __name__ == "__main__":
    sys.exit(main())
