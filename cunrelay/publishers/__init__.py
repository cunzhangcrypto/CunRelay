"""Publishers registry — build a publisher by platform name."""

from .base import BasePublisher
from .telegram import TelegramPublisher
from .threads import ThreadsPublisher
from .twitter import XPublisher


def build_publisher(platform: str, config: dict) -> BasePublisher | None:
    """Build a publisher from the publish section of config.

    Returns None when the platform is not enabled or misconfigured.
    """
    cfg = config.get("publish", {})
    if platform == "telegram":
        tg = cfg.get("telegram", {})
        if tg.get("bot_token") and tg.get("chat_id"):
            return TelegramPublisher(tg["bot_token"], tg["chat_id"])
        print("  [Publish] Telegram skipped (bot_token/chat_id not configured)")
        return None
    if platform == "x":
        x = cfg.get("x", {})
        if all(x.get(k) for k in ("api_key", "api_key_secret",
                                  "access_token", "access_token_secret")):
            return XPublisher(x["api_key"], x["api_key_secret"],
                              x["access_token"], x["access_token_secret"])
        print("  [Publish] X skipped (OAuth keys not configured)")
        return None
    if platform == "threads":
        if cfg.get("threads", {}).get("enabled"):
            return ThreadsPublisher()
        return None
    return None


def enabled_platforms(config: dict) -> list[str]:
    """Platforms that are enabled in config, in publish order."""
    cfg = config.get("publish", {})
    result = []
    for name in ("telegram", "x", "threads"):
        if name == "threads":
            if cfg.get("threads", {}).get("enabled"):
                result.append(name)
        elif cfg.get(name, {}).get("bot_token") or (
            name == "x" and cfg.get("x", {}).get("api_key")
        ):
            result.append(name)
    return result
