"""Google Sheets publish logger.

Every publish attempt (success or failure) is appended to a Google
Spreadsheet via the Sheets API using a service account.

Credentials priority:
  1. GOOGLE_SHEETS_CREDENTIALS env var (JSON string)
  2. config/credentials.json file (see config.yaml: sheets.credentials_file)

Sheets failures are never fatal: the pipeline logs a warning and keeps
going (SQLite publish_log remains the local source of truth).
"""

import json
import os
from datetime import datetime
from pathlib import Path

import pytz

HEADER = ["时间", "视频标题", "视频链接", "平台", "状态", "发布链接/消息", "内容预览"]


class SheetsLogger:
    def __init__(self, cfg: dict | None, timezone: str = "Asia/Shanghai") -> None:
        self.cfg = cfg or {}
        self.timezone = timezone
        self._client = None
        self._sheet = None
        self._warned = False

    # ── lazy init ──────────────────────────────────────────────

    @property
    def enabled(self) -> bool:
        return bool(self.cfg.get("enabled")) and bool(
            self.cfg.get("spreadsheet_id")
        )

    def _init(self) -> None:
        """Initialize gspread client + worksheet (lazy, once)."""
        if self._client is not None:
            return
        try:
            import gspread
            from google.oauth2.service_account import Credentials
        except ImportError:
            self._warn("gspread / google-auth 未安装")
            return

        info = self._load_credentials()
        if info is None:
            self._warn("Google Sheets 凭据缺失（GOOGLE_SHEETS_CREDENTIALS 或 credentials_file）")
            return

        try:
            creds = Credentials.from_service_account_info(
                info,
                scopes=["https://www.googleapis.com/auth/spreadsheets"],
            )
            self._client = gspread.authorize(creds)
            spreadsheet_id = self.cfg.get("spreadsheet_id")
            sh = self._client.open_by_key(spreadsheet_id)
            sheet_name = self.cfg.get("sheet_name", "publish_log")
            try:
                self._sheet = sh.worksheet(sheet_name)
            except Exception:
                self._sheet = sh.add_worksheet(title=sheet_name, rows=100, cols=20)
            # 确保表头存在（空表追加；已有数据但无表头则插到顶部）
            existing = self._sheet.get_all_values()
            if not existing:
                self._sheet.append_row(HEADER)
            elif existing[0] != HEADER:
                self._sheet.insert_row(HEADER, 1)
            # 把日志表移到第一个标签页，打开表格即可见
            try:
                sid = self._sheet.id
                sh.reorder_worksheets(
                    [self._sheet] + [w for w in sh.worksheets() if w.id != sid]
                )
            except Exception:
                pass
            print("  [Sheets] Google Sheets 连接成功")
        except Exception as e:
            import traceback
            self._warn(
                f"Google Sheets 初始化失败: {e}\n"
                + traceback.format_exc(limit=2)
            )

    def _load_credentials(self) -> dict | None:
        env_json = os.environ.get("GOOGLE_SHEETS_CREDENTIALS", "")
        if env_json:
            try:
                return json.loads(env_json)
            except json.JSONDecodeError:
                self._warn("GOOGLE_SHEETS_CREDENTIALS 不是合法 JSON")
                return None
        file_path = self.cfg.get("credentials_file")
        if file_path and Path(file_path).exists():
            try:
                return json.loads(Path(file_path).read_text(encoding="utf-8"))
            except Exception:
                self._warn(f"credentials_file 解析失败: {file_path}")
                return None
        return None

    def _warn(self, msg: str) -> None:
        if not self._warned:
            print(f"  [Sheets] {msg}")
            self._warned = True

    # ── logging ────────────────────────────────────────────────

    def log_publish(self, video_id: str, video_title: str, video_url: str,
                    platform: str, status: str, message: str,
                    content_preview: str = "") -> bool:
        """Append one publish record. Returns True on success."""
        if not self.enabled:
            return False
        self._init()
        if self._sheet is None:
            return False
        now = datetime.now(pytz.timezone(self.timezone)).strftime("%Y-%m-%d %H:%M:%S")
        try:
            self._sheet.append_row([
                now,
                video_title[:120],
                video_url,
                platform,
                status,
                message[:300],
                content_preview,
            ], value_input_option="USER_ENTERED")
            print(f"  [Sheets] Logged {platform} {status}")
            return True
        except Exception as e:
            self._warn(f"写入失败: {e}")
            return False
