"""``/api/clusters`` — 产业集群（含 415X 赛道与镇街级块状经济）."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from ..database import get_session
from sqlalchemy import func

from ..models import Cluster, ClusterRegion, Enterprise, EnterpriseCluster, Region
from ..schemas import (
    STUB_CLUSTER_THRESHOLD,
    ClusterDetail,
    ClusterPage,
    ClusterSummary,
    PageMeta,
)
from ..services.queries import (
    cluster_region_map,
    enterprise_cluster_names,
    enterprise_counts_by_cluster,
    grade_label,
    locate_region,
    paginate,
)

# 集群列表的排序。默认与进入这项功能之前**完全一致**（``Cluster.tier, Cluster.name``），
# 所以老链接不会因为排序上线而换一个顺序。
#
# 为什么只给这三项：页面上真正需要「比大小」的列就是这几列，其余字段（产业、
# 主产区）是**文字**，按字典序排对读者没有意义（「杭州市」排在「金华市」前面
# 只是因为字形，不是因为它更相关）。给它们做排序会把一个没用的开关塞进页面。
# 集群列表的排序键。四个键都取「读者在那一列上**实际看到的东西**」，
# 所以全部在 Python 侧按 `ClusterSummary` 算好的字段排，不在 SQL 里排：
#
#   * 「产业」列显示的是 ``l1 · l2`` 的拼接，而两类集群各缺一半 ——
#     415X 赛道只有 ``industry_l1``，调研派生产区只有 ``industry_l2``。
#     只按 l2 排，15 条赛道（l2 全是空串）会一起挤在最前面，排序等于没做。
#   * 「企业数」是 enterprise_cluster 的聚合，本来就不在 cluster 表上。
#   * 而 Postgres 不允许 ``SELECT DISTINCT`` 配一个不在 select list 里的
#     ORDER BY 表达式（按行政区筛选时确实要 DISTINCT）—— 想在 SQL 里排，
#     就得把计算列也 select 出来再拆包，为 166 行的数据不值当。
#
# 底序固定成 ``(tier, name)``：它既是 Python 稳定排序的 tiebreak
# （同企业数时按档位、名字），也保证任何排序下结果都是确定的。
SORTS = {
    "tier": "按档位",
    "name": "按集群名",
    "industry": "按产业",
    "count": "按企业数（多→少）",
}

SORT_KEYS = tuple(SORTS)


def industry_label(cluster: Cluster) -> str:
    """集群的「产业」显示串（``l1 · l2``，缺的略过）。

    **唯一出处**：列表排序与 ``ClusterRow`` 显示都从这里取 ——
    两边各拼一遍，就会出现「按产业排出来的顺序看着不像按产业排」这种
    只在一半数据上发生的问题。
    """
    return " · ".join(part for part in (cluster.industry_l1, cluster.industry_l2) if part)
# 起别名：本文件里已有一个 to_summary（集群用），直接导入会被下面的定义遮蔽
from ..services.queries import ATTRIBUTION_NOTES, to_enterprise_summary as enterprise_summary

router = APIRouter(prefix="/api/clusters", tags=["clusters"])


def to_summary(cluster: Cluster, count: int, city: str, district: str, town: str) -> ClusterSummary:
    return ClusterSummary(
        slug=cluster.slug,
        name=cluster.name,
        industry_l1=cluster.industry_l1,
        industry_l2=cluster.industry_l2,
        industry_label=industry_label(cluster),
        tier=cluster.tier,
        scale_tier=cluster.scale_tier,
        core_area=cluster.core_area,
        city=city,
        district=district,
        town=town,
        data_grade=cluster.data_grade,
        data_grade_label=grade_label(cluster.data_grade),
        enterprise_count=count,
        is_stub=count < STUB_CLUSTER_THRESHOLD,
    )


@router.get("", response_model=ClusterPage)
def list_clusters(
    session: Session = Depends(get_session),
    q: str | None = Query(None, description="集群名 / 别名 / 产业关键词"),
    tier: str | None = Query(None, pattern="^[ABC]$"),
    data_grade: str | None = None,
    city: str | None = None,
    district: str | None = None,
    town: str | None = None,
    industry_l1: str | None = None,
    sort: str = Query("tier", pattern="|".join(SORT_KEYS)),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
):
    stmt = select(Cluster)
    if q:
        like = f"%{q}%"
        stmt = stmt.where(
            or_(
                Cluster.name.ilike(like),
                Cluster.industry_l1.ilike(like),
                Cluster.industry_l2.ilike(like),
                Cluster.core_area.ilike(like),
            )
        )
    if tier:
        stmt = stmt.where(Cluster.tier == tier)
    if data_grade:
        stmt = stmt.where(Cluster.data_grade == data_grade)
    if industry_l1:
        stmt = stmt.where(Cluster.industry_l1 == industry_l1)

    if city or district or town:
        # 集群与行政区解耦，地理只作检索维度：命中任一关联行政区即返回
        target_ids = select(Region.id)
        if town:
            target_ids = target_ids.where(Region.name == town)
        elif district:
            target_ids = target_ids.where(Region.name == district)
        else:
            city_codes = select(Region.code).where(Region.name == city)
            target_ids = target_ids.where(
                or_(Region.name == city, Region.parent_code.in_(city_codes))
            )
        stmt = stmt.where(
            Cluster.id.in_(
                select(ClusterRegion.cluster_id).where(ClusterRegion.region_id.in_(target_ids))
            )
        )

    rows = session.scalars(stmt.distinct().order_by(Cluster.tier, Cluster.name)).all()
    total = len(rows)

    region_map = cluster_region_map(session, [c.id for c in rows])
    counts = enterprise_counts_by_cluster(session, [c.id for c in rows])
    cities = {r.code: r for r in session.scalars(select(Region)).all()}

    # 稳定排序：底序 (tier, name) 已经在 SQL 里定好，同键名的行因此有确定次序。
    if sort == "count":
        rows.sort(key=lambda c: -counts.get(c.id, 0))
    elif sort == "name":
        rows.sort(key=lambda c: c.name)
    elif sort == "industry":
        rows.sort(key=industry_label)
    # sort == "tier"：底序就是结果，不需要再排一遍

    start = (page - 1) * page_size
    items = []
    for cluster in rows[start: start + page_size]:
        located_city, located_district, located_town = locate_region(region_map.get(cluster.id, []), cities)
        items.append(to_summary(cluster, counts.get(cluster.id, 0), located_city, located_district, located_town))

    return ClusterPage(items=items, meta=PageMeta(**paginate(total, page, page_size)))


@router.get("/{slug}", response_model=ClusterDetail)
def get_cluster(slug: str, session: Session = Depends(get_session)):
    cluster = session.scalar(select(Cluster).where(Cluster.slug == slug))
    if cluster is None:
        raise HTTPException(status_code=404, detail="集群不存在")

    region_rows = session.execute(
        select(ClusterRegion, Region)
        .join(Region, Region.id == ClusterRegion.region_id)
        .where(ClusterRegion.cluster_id == cluster.id)
        .order_by(ClusterRegion.is_primary.desc())
    ).all()

    cities = {r.code: r for r in session.scalars(select(Region)).all()}
    regions_payload = []
    located_city = located_district = located_town = ""
    for link, region in region_rows:
        path = []
        cursor = region
        while cursor is not None:
            path.append(cursor.name)
            cursor = cities.get(cursor.parent_code or "")
        regions_payload.append(
            {
                "code": region.code,
                "name": region.name,
                "level": region.level,
                "role": link.role,
                "is_primary": link.is_primary,
                "path": " / ".join(reversed(path)),
            }
        )
        if region.level == "town":
            located_town = region.name
            parent = cities.get(region.parent_code or "")
            located_district = located_district or (parent.name if parent else "")
            grandparent = cities.get(parent.parent_code or "") if parent else None
            located_city = located_city or (grandparent.name if grandparent else "")
        elif region.level == "district":
            located_district = located_district or region.name
            parent = cities.get(region.parent_code or "")
            located_city = located_city or (parent.name if parent else "")
        elif region.level == "city":
            # 市级归口：只挂到市一级（见 seed/loader.py 的「市级归口」）
            located_city = located_city or region.name

    enterprise_rows = session.execute(
        select(Enterprise, EnterpriseCluster.attribution)
        .join(EnterpriseCluster, EnterpriseCluster.enterprise_id == Enterprise.id)
        .where(EnterpriseCluster.cluster_id == cluster.id)
        .order_by(Enterprise.name)
    ).all()
    enterprises = [row[0] for row in enterprise_rows]
    names = enterprise_cluster_names(session, [e.id for e in enterprises])

    summary = to_summary(cluster, len(enterprises), located_city, located_district, located_town)
    return ClusterDetail(
        **summary.model_dump(),
        aliases=cluster.aliases or [],
        sector=cluster.sector,
        honors=cluster.honors or [],
        metrics=cluster.metrics or [],
        summary=cluster.summary,
        # 集群可以是官方认定的，简介却可能是调研方或站方写的 —— 分别标出来
        # 简介缺档位时按「站方整理」处理：宁可按低档标，也不能因为集群是官方认定的
        # 就把一段没人签名的文字说成官方表述。
        summary_grade=cluster.summary_grade or "site_curated",
        summary_grade_label=grade_label(cluster.summary_grade or "site_curated"),
        sources=cluster.sources or [],
        verified_at=cluster.verified_at,
        regions=regions_payload,
        enterprises=[
            # 复用企业路由里的同一个序列化器。
            #
            # 这里以前是手抄一份 EnterpriseSummary 构造，结果每加一个字段都要改两处，
            # 悄悄漏掉了 founded_year / registered_capital / verify_tier / business_model。
            # 两份实现一定会漂移，所以只留一份。
            enterprise_summary(e, names.get(e.id, []), ATTRIBUTION_NOTES.get(attribution, ""))
            for e, attribution in enterprise_rows
        ],
    )