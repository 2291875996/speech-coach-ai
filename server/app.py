"""
FastAPI 应用工厂
提供 CLI 与 Web 双入口兼容：python main.py 仍可用，python run_server.py 启动 Web。
"""
import logging
import sys
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.base import BaseHTTPMiddleware

# 确保项目根目录在 sys.path 中
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from server.config import TASKS_DIR, HOST, PORT, BUILD_ID, PROTOCOL_VERSION
from server.routes import tasks as tasks_routes
from server.routes import ws as ws_routes

logger = logging.getLogger("interview_analyzer.server")


def create_app() -> FastAPI:
    app = FastAPI(
        title="AI 演讲反馈教练",
        description="基于多模态人工智能的演讲分析系统",
        version="9.0",
    )

    # 全局禁用缓存（解决前端 JS/HTML 304 问题）
    class NoCacheMiddleware(BaseHTTPMiddleware):
        async def dispatch(self, request: Request, call_next):
            response = await call_next(request)
            response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
            response.headers["Pragma"] = "no-cache"
            response.headers["Expires"] = "0"
            return response

    app.add_middleware(NoCacheMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"], allow_methods=["*"], allow_headers=["*"],
    )

    # 确保输出目录存在
    TASKS_DIR.mkdir(parents=True, exist_ok=True)

    # 注册 API 路由
    app.include_router(tasks_routes.router, prefix="/api")
    app.include_router(ws_routes.router, prefix="/ws")

    # 挂载静态文件
    static_dir = Path(__file__).resolve().parent / "static"
    if static_dir.exists():
        app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

    # ── 启动事件 ──
    @app.on_event("startup")
    async def startup():
        logger.info("=" * 50)
        logger.info("AI 演讲反馈教练 Web 服务启动")
        logger.info("监听 http://%s:%s", HOST, PORT)
        logger.info("API 文档: http://localhost:%s/docs", PORT)
        logger.info("=" * 50)

        # 预加载分析器模块
        try:
            from modules import visual_analyzer, speech_analyzer, gesture_analyzer
            logger.info("分析器模块已加载")
        except Exception as exc:
            logger.warning("分析器模块加载失败: %s", exc)

        # 预加载 Whisper 模型
        try:
            if hasattr(speech_analyzer, 'preload_whisper'):
                speech_analyzer.preload_whisper()
            logger.info("CLI 模式: python main.py video.mp4 -o output")
        except Exception as exc:
            logger.warning("Whisper 模型预加载失败（将在首次使用时加载）: %s", exc)

    @app.on_event("shutdown")
    async def shutdown():
        logger.info("服务已停止")

    # ── 页面路由（Jinja2 渲染） ──
    _setup_page_routes(app)

    return app


def _setup_page_routes(app: FastAPI):
    """注册页面路由。"""
    from fastapi.responses import HTMLResponse, JSONResponse

    template_dir = Path(__file__).resolve().parent / "templates"

    def _render(name: str, **ctx) -> HTMLResponse:
        path = template_dir / name
        if not path.exists():
            return HTMLResponse(f"<p>模板 {name} 不存在</p>", status_code=500)
        html = path.read_text(encoding="utf-8")
        # BUILD_ID 注入：替换静态资源引用
        html = html.replace('app.js?v=999999', f'app.js?v={BUILD_ID}')
        html = html.replace('dashboard.css', f'dashboard.css?v={BUILD_ID}')
        for k, v in ctx.items():
            html = html.replace("{{ " + k + " }}", str(v))
        return HTMLResponse(html)

    # ── API: 版本端点 ──
    @app.get("/api/version")
    async def api_version():
        return JSONResponse({
            "build_id": BUILD_ID,
            "protocol_version": PROTOCOL_VERSION,
            "server": "AI Speech Coach",
            "version": "9.0",
        })

    @app.get("/")
    async def index():
        return _render("index.html")

    @app.get("/tasks/{task_id}")
    async def task_page(task_id: str):
        return _render("task.html", task_id=task_id)

    @app.get("/history")
    async def history_page():
        return _render("history.html")


# 模块级 app 实例（供 uvicorn 使用）
app = create_app()
