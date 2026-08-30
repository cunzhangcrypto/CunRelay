"""X (Twitter) publisher — OAuth 1.0a, media upload + create tweet."""

from pathlib import Path
from sqlite3 import Row

import requests
from requests_oauthlib import OAuth1

from .base import BasePublisher, PublishResult

API = "https://api.x.com"
# v2 的 media upload 已被 Twitter 下线（410），必须用 v1.1 的 multipart 接口
UPLOAD_API = "https://upload.twitter.com/1.1/media/upload.json"
# X 用"加权字符数"计长度：ASCII 记 1，CJK/非 ASCII 记 2，总数上限 280。
# 纯中文内容实际只能到 140 个中文字符，再多会返回 403 Forbidden。
MAX_WEIGHTED = 280


def weighted_len(text: str) -> int:
    """按 X 规则计算加权字符数：ASCII=1，其它（中文/emoji）=2。"""
    return sum(1 if ord(ch) < 128 else 2 for ch in text)


def truncate_to_weighted(text: str, limit: int = MAX_WEIGHTED) -> str:
    """截断到加权长度不超过 ``limit``（尽量不切断 emoji 代理对）。"""
    if weighted_len(text) <= limit:
        return text
    chars = list(text)
    current = 0
    for i, ch in enumerate(chars):
        w = 1 if ord(ch) < 128 else 2
        if current + w > limit:
            return "".join(chars[:i]).rstrip()
        current += w
    return text


class XPublisher(BasePublisher):
    name = "x"

    def __init__(self, api_key: str, api_key_secret: str,
                 access_token: str, access_token_secret: str) -> None:
        # 掩码指纹：只显示尾号，方便核对 CI 加载的到底是哪套密钥，
        # 不泄露完整值。
        print(f"  [X] auth loaded: api_key=…{api_key[-6:]}, "
              f"token=…{access_token[-6:]}, "
              f"secret_len={len(access_token_secret)}")
        self.auth = OAuth1(
            api_key, api_key_secret,
            resource_owner_key=access_token,
            resource_owner_secret=access_token_secret,
        )

    def _upload_media(self, thumb: str) -> str | None:
        """Upload an image via API v1.1 and return its media_id_string."""
        try:
            with open(thumb, "rb") as f:
                media_data = f.read()
        except Exception as e:
            print(f"  [X] Media read failed: {e}")
            return None
        try:
            # v1.1 必须要 multipart/form-data（原始二进制），返回 media_id_string
            resp = requests.post(
                UPLOAD_API,
                auth=self.auth,
                files={"media": media_data},
                timeout=120,
            )
            resp.raise_for_status()
            return resp.json()["media_id_string"]
        except Exception as e:
            detail = ""
            if hasattr(e, "response") and e.response is not None:
                detail = e.response.text[:300]
            print(f"  [X] Media upload failed: {e} {detail}")
            return None

    def publish(self, post: Row) -> PublishResult:
        text = post["content"].strip()
        if weighted_len(text) > MAX_WEIGHTED:
            print(f"  [X] Truncating weighted {weighted_len(text)} -> {MAX_WEIGHTED}")
            text = truncate_to_weighted(text)

        media_id = None
        thumb = post["thumb_path"]
        if thumb and Path(thumb).exists():
            media_id = self._upload_media(thumb)

        payload = {"text": text}
        if media_id:
            payload["media"] = {"media_ids": [media_id]}

        try:
            resp = requests.post(
                f"{API}/2/tweets",
                auth=self.auth,
                json=payload,
                timeout=60,
            )
            resp.raise_for_status()
            tweet_id = resp.json()["data"]["id"]
            return PublishResult(True, f"tweet {tweet_id}",
                                 f"https://x.com/i/status/{tweet_id}")
        except Exception as e:
            detail = ""
            if hasattr(e, "response") and e.response is not None:
                detail = e.response.text[:300]
            return PublishResult(False, f"{e} {detail}".strip())
