"""Telegram publisher — sendPhoto with cover image (HTML parse mode)."""

import html
import re
from pathlib import Path
from sqlite3 import Row

import requests

from .base import BasePublisher, PublishResult

API = "https://api.telegram.org"


def _to_html(text: str) -> str:
    """Escape HTML, then convert markdown-lite (**bold**, [t](url)) to HTML."""
    text = html.escape(text)
    text = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", text)
    text = re.sub(r"\[([^\]]+)\]\((https?://[^)\s]+)\)", r'<a href="\2">\1</a>', text)
    return text


def get_chat_username(bot_token: str, chat_id: str) -> str | None:
    """通过 Bot API getChat 获取频道用户名；私有频道/失败时返回 None."""
    try:
        resp = requests.get(
            f"{API}/bot{bot_token}/getChat",
            params={"chat_id": chat_id},
            timeout=30,
        )
        resp.raise_for_status()
        return resp.json().get("result", {}).get("username")
    except Exception:
        return None


class TelegramPublisher(BasePublisher):
    name = "telegram"

    def __init__(self, bot_token: str, chat_id: str) -> None:
        self.bot_token = bot_token
        self.chat_id = chat_id

    def publish(self, post: Row) -> PublishResult:
        caption = _to_html(post["content"])
        thumb = post["thumb_path"]
        # Telegram caption limit is 1024 chars — trim hard
        if len(caption) > 1024:
            caption = caption[:1021] + "..."
        url = f"{API}/bot{self.bot_token}/sendPhoto"
        try:
            if thumb and Path(thumb).exists():
                with open(thumb, "rb") as f:
                    files = {"photo": (Path(thumb).name, f, "image/jpeg")}
                    data = {
                        "chat_id": self.chat_id,
                        "caption": caption,
                        "parse_mode": "HTML",
                    }
                    resp = requests.post(url, data=data, files=files, timeout=60)
            else:
                resp = requests.post(
                    f"{API}/bot{self.bot_token}/sendMessage",
                    json={
                        "chat_id": self.chat_id,
                        "text": caption,
                        "parse_mode": "HTML",
                    },
                    timeout=60,
                )
            resp.raise_for_status()
            body = resp.json()
            result = body.get("result", {})
            message_id = result.get("message_id")
            if message_id:
                # 公开频道优先用用户名链接（https://t.me/<username>/<msg_id>），
                # 只有私有时才回退数字 ID 格式（https://t.me/c/<id>/<msg_id>）。
                username = result.get("chat", {}).get("username")
                if username:
                    post_url = f"https://t.me/{username}/{message_id}"
                else:
                    post_url = f"https://t.me/c/{self.chat_id.lstrip('-100')}/{message_id}"
            else:
                post_url = None
            return PublishResult(True, f"telegram message #{message_id}", post_url)
        except Exception as e:
            detail = ""
            if hasattr(e, "response") and e.response is not None:
                detail = e.response.text[:300]
            return PublishResult(False, f"{e} {detail}".strip())
