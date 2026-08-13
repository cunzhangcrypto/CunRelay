"""Export records to public/data.json for the online (Cloudflare Pages) UI.

GitHub Actions 每次跑完流水线后执行导出，把 SQLite 里的发布记录
快照成一份 JSON，随静态 UI 一起部署到 Pages——这样 UI 全线上、
无需服务器，数据和 Google Sheets 互为备份。
"""

import json
import os
from datetime import datetime
from pathlib import Path

import pytz

from .scheduler import local_now
from .storage import Storage


def youtube_thumb_url(video_id: str) -> str | None:
    """YouTube 封面远程地址（UI 在线展示用，不再依赖本地文件）。"""
    vid = str(video_id or "").split(":", 1)[-1]
    return f"https://img.youtube.com/vi/{vid}/hqdefault.jpg" if vid else None


def build_sources(config: dict) -> list[dict]:
    """监控源列表（来自 follow 配置），带跳转链接。"""
    out = []
    for ch in config.get("follow", {}).get("youtube", []):
        name = ch.get("name", "")
        if ch.get("channel_id"):
            url = f"https://www.youtube.com/channel/{ch['channel_id']}"
        elif ch.get("handle"):
            url = f"https://www.youtube.com/@{ch['handle'].lstrip('@')}"
        else:
            url = None
        out.append({"type": "youtube", "name": name, "url": url})
    return out


def build_sheets(config: dict) -> dict:
    """Google Sheets 日志表信息。"""
    scfg = config.get("sheets", {})
    sid = scfg.get("spreadsheet_id")
    return {
        "enabled": bool(scfg.get("enabled") and sid),
        "url": f"https://docs.google.com/spreadsheets/d/{sid}/edit" if sid else None,
    }


def cron_to_watch_start(cron_expr: str, timezone: str) -> str | None:
    """把"每天固定时刻"的 cron（UTC）换算成部署者时区的 HH:MM。

    仅支持形如 "0 12 * * *" 的每日 cron；复杂 cron（周/月）返回 None，
    调用方回退到 config.yaml 里的 watch_start。
    """
    parts = (cron_expr or "").split()
    if len(parts) != 5 or parts[2] != "*" or parts[3] != "*" or parts[4] != "*":
        return None
    try:
        minute, hour = int(parts[0]), int(parts[1])
    except ValueError:
        return None
    try:
        tz = pytz.timezone(timezone)
    except pytz.UnknownTimeZoneError:
        return None
    # 以"今天"的 cron 时刻（UTC）换算到目标时区，跨天会自然进位
    utc_dt = datetime.utcnow().replace(
        hour=hour, minute=minute, second=0, microsecond=0, tzinfo=pytz.utc
    )
    return utc_dt.astimezone(tz).strftime("%H:%M")


def build_auto_refresh(config: dict) -> dict:
    """页面自动刷新窗口配置（前端据此决定何时轮询）。

    watch_start 优先取 CI 传入的 PIPELINE_CRON（由 relay.yml 自身推导，
    UTC 时间）换算成部署者本地时区；缺失时（本地开发/手动 export）
    回退 config.yaml 里配置的 watch_start。
    """
    ar = dict(config.get("web", {}).get("auto_refresh", {}) or {})
    cron = os.environ.get("PIPELINE_CRON", "").strip()
    if cron:
        tz = config.get("app", {}).get("timezone", "Asia/Shanghai")
        ws = cron_to_watch_start(cron, tz)
        if ws:
            ar["watch_start"] = ws
            print(f"  [Export] watch_start derived from cron '{cron}' -> {ws} ({tz})")
    return ar


def export_records(storage: Storage, config: dict, public_dir: Path) -> dict:
    """Export a full data snapshot for the web UI. Returns the data dict."""
    stats = storage.post_stats()
    breakdown = storage.post_platform_breakdown()
    done = stats.get("published", 0)
    failed = stats.get("failed", 0)
    rate = round(done / (done + failed) * 100) if (done + failed) else None

    posts = [dict(r) for r in storage.posts(limit=5000)]
    for p in posts:
        p["thumb_url"] = youtube_thumb_url(p["video_id"])

    data = {
        "generated_at": local_now(config).strftime("%Y-%m-%d %H:%M:%S"),
        "sources": build_sources(config),
        "sheets": build_sheets(config),
        "auto_refresh": build_auto_refresh(config),
        "stats": stats,
        "breakdown": breakdown,
        "success_rate": rate,
        "posts": posts,
        "logs": [dict(r) for r in storage.logs(limit=5000)],
    }

    public_dir.mkdir(parents=True, exist_ok=True)
    target = public_dir / "data.json"
    target.write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"  [Export] data.json written ({len(posts)} posts, {len(data['logs'])} logs)")
    return data
