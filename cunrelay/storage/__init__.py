"""SQLite storage for CunRelay.

Three tables:
  - seen_videos : video deduplication (which videos were discovered)
  - posts       : per-platform post queue (content + schedule + status)
  - publish_log : publish attempt records (success / failure)
"""

import sqlite3
from datetime import datetime
from pathlib import Path

POST_QUEUED = "queued"
POST_PUBLISHED = "published"
POST_FAILED = "failed"


class Storage:
    """Lightweight SQLite storage for dedup, queue and logging."""

    def __init__(self, db_path: str) -> None:
        self.db_path = db_path
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        # check_same_thread=False: Web UI 在 FastAPI 线程池中复用同一连接
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._init_db()

    def _init_db(self) -> None:
        self._conn.executescript("""
            CREATE TABLE IF NOT EXISTS seen_videos (
                item_id TEXT PRIMARY KEY,
                source TEXT NOT NULL,
                source_name TEXT NOT NULL,
                title TEXT NOT NULL,
                url TEXT NOT NULL,
                thumb_path TEXT,
                first_seen TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS posts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                video_id TEXT NOT NULL,
                video_title TEXT NOT NULL,
                video_url TEXT NOT NULL,
                platform TEXT NOT NULL,
                content TEXT NOT NULL,
                thumb_path TEXT,
                status TEXT NOT NULL DEFAULT 'queued',
                send_at TEXT NOT NULL,
                published_at TEXT,
                retry_count INTEGER NOT NULL DEFAULT 0,
                error TEXT
            );

            CREATE TABLE IF NOT EXISTS publish_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                post_id INTEGER,
                video_id TEXT,
                video_title TEXT,
                video_url TEXT,
                platform TEXT,
                status TEXT NOT NULL,
                message TEXT,
                created_at TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_posts_status ON posts(status);
            CREATE INDEX IF NOT EXISTS idx_posts_send_at ON posts(send_at);
            CREATE INDEX IF NOT EXISTS idx_posts_video_platform
                ON posts(video_id, platform);
            CREATE INDEX IF NOT EXISTS idx_log_created_at
                ON publish_log(created_at);
        """)
        self._conn.commit()

    def _now(self) -> str:
        return datetime.now().isoformat(timespec="seconds")

    # ── seen_videos ────────────────────────────────────────────

    def is_new_video(self, item_id: str) -> bool:
        cur = self._conn.execute(
            "SELECT 1 FROM seen_videos WHERE item_id = ?", (item_id,)
        )
        return cur.fetchone() is None

    def mark_video_seen(self, item_id: str, source: str, source_name: str,
                        title: str, url: str, thumb_path: str | None) -> None:
        self._conn.execute(
            "INSERT OR IGNORE INTO seen_videos VALUES (?, ?, ?, ?, ?, ?, ?)",
            (item_id, source, source_name, title, url, thumb_path, self._now()),
        )
        self._conn.commit()

    def update_thumb(self, item_id: str, thumb_path: str) -> None:
        self._conn.execute(
            "UPDATE seen_videos SET thumb_path = ? WHERE item_id = ?",
            (thumb_path, item_id),
        )
        self._conn.commit()

    def remove_seen(self, item_id: str) -> None:
        """删除 seen 标记（处理失败时回滚，让下一轮重新尝试）。"""
        self._conn.execute(
            "DELETE FROM seen_videos WHERE item_id = ?", (item_id,)
        )
        self._conn.commit()

    def get_thumb(self, item_id: str) -> str | None:
        cur = self._conn.execute(
            "SELECT thumb_path FROM seen_videos WHERE item_id = ?", (item_id,)
        )
        row = cur.fetchone()
        return row[0] if row else None

    # ── posts ──────────────────────────────────────────────────

    def has_post(self, video_id: str, platform: str) -> bool:
        cur = self._conn.execute(
            "SELECT 1 FROM posts WHERE video_id = ? AND platform = ?",
            (video_id, platform),
        )
        return cur.fetchone() is not None

    def has_any_post(self, video_id: str) -> bool:
        """posts 表是否已有该视频的任何入队记录（不限平台/状态）。"""
        cur = self._conn.execute(
            "SELECT 1 FROM posts WHERE video_id = ? LIMIT 1", (video_id,)
        )
        return cur.fetchone() is not None

    def has_success(self, video_id: str, platform: str | None = None) -> bool:
        """publish_log 中该视频是否已有成功发布记录（可按平台过滤）。

        以「真实成功发布过」为补发判据：即使 posts 表因缓存/中断与
        seen_videos 不一致，只要该平台有过 success 记录，就绝不补发，
        防止重复发送。
        """
        if platform:
            cur = self._conn.execute(
                "SELECT 1 FROM publish_log WHERE video_id = ?"
                " AND platform = ? AND status = 'success' LIMIT 1",
                (video_id, platform),
            )
        else:
            cur = self._conn.execute(
                "SELECT 1 FROM publish_log WHERE video_id = ?"
                " AND status = 'success' LIMIT 1",
                (video_id,),
            )
        return cur.fetchone() is not None

    def create_post(self, video_id: str, video_title: str, video_url: str,
                    platform: str, content: str, thumb_path: str | None,
                    send_at: str) -> int:
        cur = self._conn.execute(
            "INSERT INTO posts (video_id, video_title, video_url, platform,"
            " content, thumb_path, status, send_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (video_id, video_title, video_url, platform, content,
             thumb_path, POST_QUEUED, send_at),
        )
        self._conn.commit()
        return cur.lastrowid

    def due_posts(self, now: str) -> list[sqlite3.Row]:
        cur = self._conn.execute(
            "SELECT * FROM posts WHERE status = ? AND send_at <= ?"
            " ORDER BY send_at ASC",
            (POST_QUEUED, now),
        )
        return cur.fetchall()

    def posts(self, limit: int = 100, platform: str | None = None,
              status: str | None = None) -> list[sqlite3.Row]:
        sql = "SELECT * FROM posts WHERE 1=1"
        args: list = []
        if platform and platform != "all":
            sql += " AND platform = ?"
            args.append(platform)
        if status and status != "all":
            sql += " AND status = ?"
            args.append(status)
        sql += " ORDER BY id DESC LIMIT ?"
        args.append(limit)
        cur = self._conn.execute(sql, args)
        return cur.fetchall()

    def post_stats(self) -> dict:
        cur = self._conn.execute(
            "SELECT status, COUNT(*) AS n FROM posts GROUP BY status"
        )
        rows = cur.fetchall()
        stats = {POST_QUEUED: 0, POST_PUBLISHED: 0, POST_FAILED: 0}
        for row in rows:
            if row["status"] in stats:
                stats[row["status"]] = row["n"]
        return stats

    def post_platform_breakdown(self) -> dict:
        """{status: {platform: count}} — 每个状态细分到平台."""
        cur = self._conn.execute(
            "SELECT status, platform, COUNT(*) AS n"
            " FROM posts GROUP BY status, platform"
        )
        result: dict[str, dict[str, int]] = {}
        for row in cur.fetchall():
            result.setdefault(row["status"], {})[row["platform"]] = row["n"]
        return result

    def mark_published(self, post_id: int, published_at: str) -> None:
        self._conn.execute(
            "UPDATE posts SET status = ?, published_at = ?, error = NULL"
            " WHERE id = ?",
            (POST_PUBLISHED, published_at, post_id),
        )
        self._conn.commit()

    def mark_failed(self, post_id: int, error: str, retry_count: int,
                    new_send_at: str | None, final: bool) -> None:
        if final:
            self._conn.execute(
                "UPDATE posts SET status = ?, error = ?, retry_count = ?"
                " WHERE id = ?",
                (POST_FAILED, error[:500], retry_count, post_id),
            )
        else:
            self._conn.execute(
                "UPDATE posts SET error = ?, retry_count = ?, send_at = ?"
                " WHERE id = ?",
                (error[:500], retry_count, new_send_at, post_id),
            )
        self._conn.commit()

    # ── publish_log ────────────────────────────────────────────

    def add_log(self, post_id: int | None, video_id: str, video_title: str,
                video_url: str, platform: str, status: str,
                message: str) -> None:
        self._conn.execute(
            "INSERT INTO publish_log (post_id, video_id, video_title,"
            " video_url, platform, status, message, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (post_id, video_id, video_title, video_url, platform, status,
             message[:500], self._now()),
        )
        self._conn.commit()

    def logs(self, limit: int = 200) -> list[sqlite3.Row]:
        cur = self._conn.execute(
            "SELECT * FROM publish_log ORDER BY id DESC LIMIT ?", (limit,)
        )
        return cur.fetchall()

    def log_stats(self) -> dict:
        cur = self._conn.execute(
            "SELECT status, COUNT(*) AS n FROM publish_log GROUP BY status"
        )
        rows = cur.fetchall()
        stats = {"success": 0, "failed": 0}
        for row in rows:
            if row["status"] in stats:
                stats[row["status"]] = row["n"]
        return stats

    def migrate_telegram_links(self, chat_id: str, username: str | None) -> int:
        """把 publish_log 里旧的 t.me/c/<数字ID>/ 链接修正为公开用户名格式。

        频道为公开且能查到 username 时才有意义；私有频道传入 None 直接跳过。
        返回修正的行数。
        """
        if not username:
            return 0
        old_prefix = f"https://t.me/c/{chat_id.lstrip('-100')}/"
        new_prefix = f"https://t.me/{username}/"
        cur = self._conn.execute(
            "UPDATE publish_log SET message = REPLACE(message, ?, ?)"
            " WHERE message LIKE ?",
            (old_prefix, new_prefix, f"%{old_prefix}%"),
        )
        self._conn.commit()
        return cur.rowcount

    def close(self) -> None:
        self._conn.close()
