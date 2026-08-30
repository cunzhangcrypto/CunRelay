"""X (Twitter) publisher — OAuth 1.0a, media upload + create tweet."""

from pathlib import Path
from sqlite3 import Row

import requests
from requests_oauthlib import OAuth1

from .base import BasePublisher, PublishResult

API = "https://api.x.com"
# v2 的 media upload 已被 Twitter 下线（410），必须用 v1.1 的 multipart 接口
UPLOAD_API = "https://upload.twitter.com/1.1/media/upload.json"
MAX_CHARS = 280


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
        if len(text) > MAX_CHARS:
            print(f"  [X] Truncating {len(text)} -> {MAX_CHARS} chars")
            text = text[:MAX_CHARS]

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
