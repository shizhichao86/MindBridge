from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles

from app.api.routes import router
from app.core.bootstrap import create_schema, seed_data
from app.core.config import get_settings
from app.core.database import SessionLocal
from app.core.logging import get_logger, setup_logging, timed
from app.services.tool_queue import get_tool_queue_worker

logger = get_logger("main")


def create_app() -> FastAPI:
    app = FastAPI(title="MindBridge Python", version="0.1.0")

    @app.middleware("http")
    async def log_request(request: Request, call_next):
        """记录每个 HTTP 请求的方法/路径/状态码/耗时."""
        with timed(f"{request.method} {request.url.path}", logger=logger):
            response = await call_next(request)
        return response

    @app.middleware("http")
    async def no_cache_frontend_assets(request, call_next):
        response = await call_next(request)
        path = request.url.path
        if path == "/" or path.endswith((".html", ".js", ".css")):
            response.headers["Cache-Control"] = "no-store"
        return response

    @app.on_event("startup")
    def startup() -> None:
        setup_logging()
        logger.info("MindBridge 启动中...")
        with timed("create_schema + seed_data", logger=logger):
            create_schema()
            db = SessionLocal()
            try:
                seed_data(db)
            finally:
                db.close()
        worker = get_tool_queue_worker(get_settings())
        worker.start()
        app.state.tool_queue_worker = worker
        logger.info("MindBridge 启动完成 | http://127.0.0.1:8080")

    @app.on_event("shutdown")
    def shutdown() -> None:
        logger.info("MindBridge 停止中...")
        worker = getattr(app.state, "tool_queue_worker", None)
        if worker is not None:
            worker.stop()

    app.include_router(router)
    static_dir = Path(__file__).resolve().parent / "static"
    app.mount("/", StaticFiles(directory=static_dir, html=True), name="static")
    return app


app = create_app()
