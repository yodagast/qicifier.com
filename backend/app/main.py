"""FastAPI application: 浙江产业带企业库 API."""

from __future__ import annotations

import logging

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import text

from .config import settings
from .database import engine
from .models import Base
from . import auth as auth_module
from .routers import (
    account,
    admin,
    admin_clusters,
    admin_lists,
    clusters,
    enterprises,
    regions,
    research,
    sitemap,
    stats,
    tracks,
)

app = FastAPI(
    title=settings.site_name,
    description=(
        "浙江产业带企业库：以官方名单为骨架、逐条标注核实程度的产业集群与企业检索服务。"
        "排序只依据公开可核验程度与资料完整度，对所有企业一致。"
    ),
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

logger = logging.getLogger("zanzhu")


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """未捕获异常也返回 JSON，而不是 Starlette 默认的纯文本 `Internal Server Error`。

    前端（以及任何脚本）失败时都要能读到 `detail`，否则 `JSON.parse` 会抛出
    `Unexpected token 'I'` —— 把「服务端 500」显示成一个看不懂的语法错误。
    纯文本 body 只说得出「坏了」，JSON 至少能说清是哪个请求坏了。
    """
    logger.exception("未处理异常：%s %s", request.method, request.url.path)
    return JSONResponse(
        status_code=500,
        content={"detail": f"服务端处理 {request.method} {request.url.path} 时出错：{type(exc).__name__}"},
    )


app.include_router(stats.router)
app.include_router(tracks.router)
app.include_router(clusters.router)
app.include_router(enterprises.router)
app.include_router(regions.router)
app.include_router(admin.router)
app.include_router(admin_lists.router)
app.include_router(admin_clusters.router)
app.include_router(account.router)
# 行业研究文档（/research 与 /api/research）与 sitemap（/sitemap.xml）。
# 三者都不在 /api 前缀下：它们的 URL 要直接给搜索引擎与读者用。
app.include_router(research.router)
app.include_router(research.admin_router)
app.include_router(sitemap.router)


@app.get("/api/health", tags=["meta"])
def health():
    try:
        with engine.connect() as connection:
            connection.execute(text("select 1"))
        database = "up"
    except Exception as exc:  # pragma: no cover - operational probe
        return JSONResponse(status_code=503, content={"status": "degraded", "database": str(exc)})
    return {
        "status": "ok",
        "database": database,
        "site": settings.site_name,
        # 版本判据：线上「改了代码但没重启后端」时，/api/health 与 /api/clusters
        # 这类旧接口返回 200 说明不了任何问题。运维只需要看这里有没有本版新增的能力。
        "features": ["clusters", "enterprises", "regions", "tracks", "research", "sitemap"],
    }


@app.on_event("startup")
def on_startup() -> None:
    # 建全部表：models.Base + auth（User/Favorite 注册在 app.auth 上）
    Base.metadata.create_all(bind=engine)