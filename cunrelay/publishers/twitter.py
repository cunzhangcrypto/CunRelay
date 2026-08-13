"""X (Twitter) publisher — OAuth 1.0a, media upload + create tweet."""

import base64
from pathlib import Path
from sqlite3 import Row

import requests
from requests_oauthlib import OAuth1

from .base import BasePublisher, PublishResult

API = "https://api.x.com"
UPLOAD_API = "https://upload.x.com"
MAX_CHARS = 280


class XPublisher(BasePublisher):
    name = "x"

    def __init__(self, api_key: str, api_key_secret: str,
                 access_token: str, access_token_secret: str) -> None:
        self.auth = OAuth1(
            api_key, api_key_secret,
            resource_owner_key=access_token,
            resource_owner_secret=access_token_secret,
        )

    def _upload_media(self, thumb: str) -> str | None:
        """Upload an image and return its media_id."""
        try:
            with open(thumb, "rb") as f:
                media_data = base64.b64encode(f.read()).decode("ascii")
        except Exception as e:
            print(f"  [X] Media read failed: {e}")
            return None
        try:
            resp = requests.post(
                f"{UPLOAD_API}/2/media/upload",
                auth=self.auth,
                json={"media_data": media_data, "media_category": "tweet_image"},
                timeout=120,
            )
            resp.raise_for_status()
            return resp.json()["data"]["id"]
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
