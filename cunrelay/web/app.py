"""FastAPI web app — 科技感浅色控制台，展示发布队列与发布日志。

本地开发用（uv run python -m cunrelay serve）。线上部署时 UI 改为
静态部署到 Cloudflare Pages，直接读取 public/data.json（由 export 生成），
不再依赖本接口。
"""

from pathlib import Path

from fastapi import FastAPI, Query
from fastapi.staticfiles import StaticFiles

from ..config import project_root
from ..export import build_auto_refresh, build_sheets, build_sources
from ..storage import Storage

PUBLIC_DIR = project_root() / "public"


def create_app(config: dict) -> FastAPI:
    output_dir = Path(config.get("app", {}).get("output_dir", "output"))
    db_path = str(output_dir / "cunrelay.db")
    storage = Storage(db_path)

    app = FastAPI(title="CunRelay", docs_url=None, redoc_url=None)

    @app.get("/api/stats")
    def stats():
        posts = storage.post_stats()
        logs = storage.log_stats()
        total = sum(posts.values())
        done = posts.get("published", 0)
        failed = posts.get("failed", 0)
        rate = round(done / (done + failed) * 100) if (done + failed) else None
        return {
            "posts": posts,
            "breakdown": storage.post_platform_breakdown(),
            "logs": logs,
            "total": total,
            "success_rate": rate,
        }

    @app.get("/api/sources")
    def sources():
        return build_sources(config)

    @app.get("/api/sheets")
    def sheets():
        return build_sheets(config)

    @app.get("/api/config")
    def cfg():
        return {"auto_refresh": build_auto_refresh(config)}

    @app.get("/api/posts")
    def posts(limit: int = Query(100, ge=1, le=5000),
              platform: str = Query("all"),
              status: str = Query("all")):
        rows = storage.posts(limit, platform, status)
        return [dict(r) for r in rows]

    @app.get("/api/logs")
    def logs(limit: int = Query(200, ge=1, le=5000)):
        rows = storage.logs(limit)
        return [dict(r) for r in rows]

    # 静态资源（public/ 目录，含 index.html）
    app.mount("/", StaticFiles(directory=str(PUBLIC_DIR), html=True), name="public")
    return app
