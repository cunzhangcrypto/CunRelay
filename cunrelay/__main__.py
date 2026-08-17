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
from .publishers import enabled_platforms
from .scheduler import enqueue_video, local_now, send_due
from .sheets import SheetsLogger
from .storage import Storage


def _filter_by_age(items, max_hours, now):
    """Keep only videos published within the last ``max_hours``."""
    if max_hours <= 0:
        return items
    cutoff = now - timedelta(hours=max_hours)
    return [it for it in items if it.published and it.published >= cutoff]


def _extract_youtube_id(url: str) -> str | None:
    """从 YouTube 链接提取视频 ID（watch?v=/youtu.be//shorts//live/）。"""
    import re
    m = re.search(r"(?:v=|youtu\.be/|shorts/|live/)([\w-]{11})", url or "")
    return m.group(1) if m else None


def _parse_sheet_time(s: str) -> str:
    """'2026-08-13 20:15:18' → naive ISO 字符串（本地时区）。"""
    try:
        return datetime.strptime(s.strip(), "%Y-%m-%d %H:%M:%S").isoformat(
            timespec="seconds")
    except Exception:
        return datetime.now().isoformat(timespec="seconds")


def _restore(storage: Storage, config: dict) -> int:
    """从 Google Sheets 把历史发布记录补回 SQLite（幂等，可反复执行）。

    线上 DB 因缓存/意外丢失记录后，用 Sheets 这份备份恢复 UI 展示。
    同一视频+平台只保留最早一条成功记录——历史重复发布（如补发 bug
    产生的重复消息）不会被重复展示。返回恢复的发布记录条数。
    """
    try:
        from .sheets import SheetsLogger
        scfg = config.get("sheets", {})
        if not (scfg.get("enabled") and scfg.get("spreadsheet_id")):
            print("  [Restore] Sheets 未配置，跳过")
            return 0
        sheets = SheetsLogger(scfg, config.get("app", {}).get("timezone", "Asia/Shanghai"))
        records = sheets.read_records()
    except Exception as e:
        print(f"  [Restore] skip: {e}")
        return 0

    if not records:
        print("  [Restore] Sheets 无记录，跳过")
        return 0

    # 去重：同一 视频链接+平台 只保留最早一条成功记录
    best: dict[tuple, dict] = {}
    for r in records:
        if r.get("status") != "success":
            continue
        key = (r.get("video_url") or "", r.get("platform") or "")
        if key in best and (r.get("time") or "") >= (best[key].get("time") or ""):
            continue
        best[key] = r

    restored = 0
    for (video_url, platform), r in best.items():
        vid = _extract_youtube_id(video_url)
        if not vid:
            continue
        item_id = f"yt:{vid}"
        title = r.get("video_title") or "（已恢复的历史记录）"
        # seen_videos：防止 collect 把它当新视频重新采集+发送
        storage.mark_video_seen(item_id, "youtube", "恢复", title, video_url, None)
        # posts / publish_log 已有记录则跳过
        if storage.has_post(item_id, platform) or storage.has_success(item_id, platform):
            continue
        published_at = _parse_sheet_time(r.get("time"))
        post_id = storage.create_post(
            video_id=item_id,
            video_title=title,
            video_url=video_url,
            platform=platform,
            content=(r.get("content_preview") or ""),
            thumb_path=None,
            send_at=published_at,
        )
        storage.mark_published(post_id, published_at)
        storage.add_log(post_id, item_id, title, video_url, platform,
                        "success", r.get("message") or "")
        restored += 1
        print(f"  [Restore] + {platform}: {title[:60]}")
    if restored:
        print(f"  [Restore] 恢复 {restored} 条发布记录（来自 Google Sheets）")
    return restored


def _migrate_telegram_links(storage: Storage, config: dict) -> None:
    """把 publish_log 里旧格式 t.me/c/<数字ID>/ 链接修正为公开用户名格式。

    放在 restore 之后执行，保证恢复写入的链接同样被修正。
    私有频道（getChat 拿不到 username）时跳过。
    """
    try:
        tg = config.get("publish", {}).get("telegram", {})
        if not (tg.get("bot_token") and tg.get("chat_id")):
            return
        from .publishers.telegram import get_chat_username
        username = get_chat_username(tg["bot_token"], str(tg["chat_id"]))
        n = storage.migrate_telegram_links(str(tg["chat_id"]), username)
        if n:
            print(f"  [Migrate] 修正 {n} 条 Telegram 历史链接"
                  f" (https://t.me/{username}/)")
    except Exception as e:
        print(f"  [Migrate] skip: {e}")


def _enrich_and_enqueue(storage: Storage, item, config: dict,
                        thumb_dir: Path, ai_cfg: dict, api_key: str | None) -> bool:
    """抓字幕/封面 → AI 生成 → 入队。

    enqueue_video 内部按「视频 + 平台」判重（has_post），幂等：
    已入队的平台自动跳过，只补齐缺失的平台。

    返回是否成功入队；失败（无 key / AI 生成失败）时返回 False，
    调用方应回滚 seen 标记，让下一轮重新尝试，避免视频被永久跳过。
    """
    video_id = item.item_id.split(":", 1)[-1]
    transcript = fetch_transcript(video_id, ai_cfg.get("max_transcript_chars", 6000))
    if transcript:
        print(f"    transcript: {len(transcript)} chars")

    thumb = download_thumbnail(video_id, str(thumb_dir))
    if thumb:
        storage.update_thumb(item.item_id, thumb)

    if not api_key:
        print("    (skipped AI copy — no API key)")
        return False
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
        # DeepSeek 偶发输出不完整/截断，立即重试一次成功率很高
        print("    [AI] 首次生成失败，重试一次…")
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
        print(f"    AI copy failed for '{item.title}', skipped")
        return False
    created = enqueue_video(storage, item, copy, config, thumb)
    print(f"    enqueued {created} posts")
    return created > 0


def _recover_stuck_seen(storage: Storage, config: dict) -> None:
    """清理"已 seen 但从未成功入队"的孤儿标记。

    历史 bug 会让视频在 AI 生成失败时被 mark_video_seen 后永久跳过
    （如昨天"跨境多账号总被关联"那条）。这里把 72h 窗口内 seen 过、
    但 posts 表没有任何记录的视频标记删除，下一轮重新处理。
    只清理窗口内的，避免把太老的视频重新拉起来。
    """
    max_age = int(config.get("app", {}).get("max_item_age_hours", 72))
    cutoff = (local_now(config) - timedelta(hours=max_age)).isoformat(
        timespec="seconds")
    rows = storage._conn.execute(
        "SELECT item_id, title FROM seen_videos WHERE first_seen >= ?",
        (cutoff,),
    ).fetchall()
    removed = 0
    for row in rows:
        item_id = row["item_id"]
        if storage.has_any_post(item_id):
            continue
        storage.remove_seen(item_id)
        removed += 1
        print(f"  [Recover] 清除未入队的 seen 标记，重新处理: {row['title'][:60]}")
    if removed:
        print(f"  [Recover] 共恢复 {removed} 个此前失败跳过的视频")


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
    # 自我修复：清理历史上"AI 失败后遗留"的孤儿 seen 标记，让它们重试
    _recover_stuck_seen(storage, config)
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
        storage.mark_video_seen(item.item_id, item.source, item.source_name,
                                item.title, item.url, None)
        if not _enrich_and_enqueue(storage, item, config, thumb_dir, ai_cfg, api_key):
            # 处理失败（如 AI 生成不完整）：回滚 seen 标记，
            # 下一轮自动重试，避免"已 seen 但从未发送"的视频被永久跳过。
            storage.remove_seen(item.item_id)
            print("  → 处理失败，已回滚标记，下一轮将重试")

    # ── 补发缺失平台 ────────────────────────────────────────────
    # 72h 窗口内已 seen 的视频，若某个当前启用的平台「从未成功发布过」，
    # 重新入队补齐（例如：今天只发了 Telegram，明天启用 X 后补发 X）。
    # 判据用 publish_log 的 success 记录（has_success），而不是 posts 表：
    # posts 可能因缓存/中断与 seen 不一致，若某平台已有 success 记录
    # （哪怕 posts 记录丢失），绝不补发，防止重复发送。
    # enqueue_video 幂等：已入队的平台自动跳过，只补缺失平台。
    for it in aged:
        if storage.is_new_video(it.item_id):
            continue
        # 只有「确认成功发布过至少一个平台」的视频才参与补发：
        # 从未成功发布过的（如 AI 失败被 seen、或 DB 状态不全），
        # 一律不进入补发路径，避免把"漏发重试"误当"平台补发"反复发送。
        if not storage.has_success(it.item_id):
            continue
        missing = [p for p in enabled_platforms(config)
                   if not storage.has_success(it.item_id, p)]
        if not missing:
            continue
        print(f"\n  → 补发缺失平台 {missing}: {it.title}")
        _enrich_and_enqueue(storage, it, config, thumb_dir, ai_cfg, api_key)


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
                        choices=["collect", "send", "export", "serve", "restore", "all"],
                        help="collect=采集+AI+入队, send=发送到点排期, export=导出UI数据, restore=从Sheets恢复历史记录, serve=本地Web UI, all=全流程")
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
        # 先从 Sheets 恢复缺失的历史记录（幂等），再采集新视频
        _restore(storage, config)
    if args.command == "restore":
        _restore(storage, config)
    # Telegram 历史链接归一化：在 restore 之后执行，保证恢复写入的
    # 链接同样被修正（旧格式 t.me/c/<数字ID>/ 公开频道点不开）。
    _migrate_telegram_links(storage, config)
    if args.command in ("all", "collect"):
        _collect(storage, config)
    if args.command == "restore":
        _export(storage, config)
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
