"""FastAPI application: 浙江产业带企业库 API."""

from __future__ import annotations

from fastapi import FastAPI
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

app.include_router(stats.router)
app.include_router(tracks.router)
app.include_router(clusters.router)
app.include_router(enterprises.router)
app.include_router(regions.router)
app.include_router(admin.router)
app.include_router(admin_lists.router)
app.include_router(admin_clusters.router)
app.include_router(account.router)


@app.get("/api/health", tags=["meta"])
def health():
    try:
        with engine.connect() as connection:
            connection.execute(text("select 1"))
        database = "up"
    except Exception as exc:  # pragma: no cover - operational probe
        return JSONResponse(status_code=503, content={"status": "degraded", "database": str(exc)})
    return {"status": "ok", "database": database, "site": settings.site_name}


@app.on_event("startup")
def on_startup() -> None:
    # 建全部表：models.Base + auth（User/Favorite 注册在 app.auth 上）
    Base.metadata.create_all(bind=engine)