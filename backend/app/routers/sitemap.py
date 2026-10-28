"""``/sitemap.xml`` — 由数据库生成，不手写静态清单。

为什么不在构建时生成静态文件：集群与企业是**灌库之后才会变**的，静态 sitemap
必然滞后于库，而「sitemap 里有的 URL 一定存在」是它唯一的价值。

薄页（``is_stub``，挂载企业 < 10 家）不进 sitemap：把 94 个空壳页提交上去，
等于主动告诉搜索引擎「本站有大量废页」，会拉低整站质量判定；等企业数补到
10 家以上会自动出现，不需要人工维护名单。
"""

from __future__ import annotations

from datetime import datetime
from urllib.parse import quote

from fastapi import APIRouter, Depends, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..database import get_session
from ..models import Cluster
from ..schemas import STUB_CLUSTER_THRESHOLD
from ..services.queries import enterprise_counts_by_cluster
from .research import published_articles

router = APIRouter(tags=["meta"])

BASE = "https://qicifier.com"

# 静态页：首页 + 列表 + 说明页 + 行业研究入口。
# 登录 / 收藏 / 后台刻意不在里面（它们带 noindex，见 nginx 与前端路由 meta）。
STATIC_PATHS = [
    ("/", "weekly", "1.0"),
    ("/map", "monthly", "0.8"),
    ("/clusters", "weekly", "0.9"),
    ("/enterprises", "weekly", "0.9"),
    ("/research", "weekly", "0.8"),
    ("/about", "monthly", "0.6"),
    ("/company", "monthly", "0.5"),
    ("/service", "monthly", "0.4"),
]


def _url(loc: str, lastmod: str = "", freq: str = "monthly", prio: str | None = None) -> str:
    parts = [f"<loc>{loc}</loc>"]
    if lastmod:
        parts.append(f"<lastmod>{lastmod}</lastmod>")
    parts.append(f"<changefreq>{freq}</changefreq>")
    if prio:
        parts.append(f"<priority>{prio}</priority>")
    return "<url>" + "".join(parts) + "</url>"


def _date(value: datetime | None) -> str:
    return value.strftime("%Y-%m-%d") if value else ""


@router.get("/sitemap.xml", include_in_schema=False)
def sitemap(session: Session = Depends(get_session)) -> Response:
    urls = [
        _url(f"{BASE}{path}", freq=freq, prio=prio)
        for path, freq, prio in STATIC_PATHS
    ]

    clusters = session.scalars(select(Cluster).order_by(Cluster.slug)).all()
    counts = enterprise_counts_by_cluster(session, [cluster.id for cluster in clusters])
    for cluster in clusters:
        if counts.get(cluster.id, 0) < STUB_CLUSTER_THRESHOLD:
            continue
        # 集群 slug 带中文（``415X-人工智能``），必须百分号编码：XML 不接受裸中文 URL
        loc = f"{BASE}/clusters/{quote(cluster.slug, safe='')}"
        urls.append(_url(loc, _date(cluster.updated_at), "monthly", "0.7"))

    for article in published_articles():
        loc = f"{BASE}/research/{quote(article['slug'], safe='')}"
        urls.append(_url(loc, article.get("updated_at", ""), "monthly", "0.6"))

    body = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
        + "".join(urls)
        + "</urlset>"
    )
    return Response(body, media_type="application/xml")
