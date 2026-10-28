"""``/api/enterprises`` — 企业检索。

Four entry points required by the plan (§07 step 4): 按产品 / 按集群 / 按镇街 / 按核实程度.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import String, cast, func, or_, select
from sqlalchemy.orm import Session

from ..database import get_session
from ..models import Cluster, Enterprise, EnterpriseCluster
from ..schemas import EnterpriseDetail, EnterprisePage, EnterpriseSummary, PageMeta
from ..services.queries import (
    ATTRIBUTION_NOTES,
    enterprise_cluster_names,
    grade_label,
    paginate,
    pending_attribution,
    to_enterprise_summary,
)

router = APIRouter(prefix="/api/enterprises", tags=["enterprises"])

SORTS = {
    "relevance": None,
    "name": Enterprise.name.asc(),
    "name_desc": Enterprise.name.desc(),
    "district": Enterprise.district.asc(),
    "completeness": None,
}

def completeness(enterprise: Enterprise) -> int:
    """资料完整度（0-100）。排序辅助项，不代表企业优劣。"""
    fields = [
        bool(enterprise.website),
        bool(enterprise.public_phone),
        bool(enterprise.public_email),
        bool(enterprise.products),
        bool(enterprise.list_names),
    ]
    return round(100 * sum(fields) / len(fields))


@router.get("", response_model=EnterprisePage)
def list_enterprises(
    session: Session = Depends(get_session),
    q: str | None = Query(None, description="企业名 / 产品 / 地址 关键词"),
    product: str | None = Query(None, description="按产品关键词"),
    cluster: str | None = Query(None, description="按集群 slug"),
    city: str | None = None,
    district: str | None = None,
    town: str | None = None,
    data_grade: str | None = Query(None, description="官方认定 / 政府公开信息 / 媒体 · 协会来源 / 站方整理 / 企业自报 · 本站未核验"),
    scope: str = Query(
        "all",
        pattern="^(all|zhejiang|out_of_province)$",
        description="产地范围：all=全部；zhejiang=只看浙江（排除省外对比产区）；out_of_province=只看省外对比产区",
    ),
    has_contact: bool = Query(False, description="只看有官网/电话/邮箱/地址之一的企业"),
    contact_status: str | None = Query(
        None,
        pattern="^(full|partial|empty)$",
        description="full=官网+联系方式齐全；partial=仅有其一；empty=待补（电话/邮箱/地址都算联系方式）",
    ),
    sort: str = Query("relevance", pattern="|".join(SORTS)),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
):
    stmt = select(Enterprise)
    if q:
        like = f"%{q}%"
        stmt = stmt.where(
            or_(
                Enterprise.name.ilike(like),
                Enterprise.summary.ilike(like),
                Enterprise.district.ilike(like),
                cast(Enterprise.products, String).ilike(like),
            )
        )
    if product:
        stmt = stmt.where(cast(Enterprise.products, String).ilike(f"%{product}%"))
    if city:
        stmt = stmt.where(Enterprise.city == city)
    if district:
        stmt = stmt.where(Enterprise.district == district)
    if town:
        stmt = stmt.where(Enterprise.town == town)
    if data_grade:
        stmt = stmt.where(Enterprise.data_grade == data_grade)

    if cluster:
        stmt = stmt.join(EnterpriseCluster, EnterpriseCluster.enterprise_id == Enterprise.id).join(
            Cluster, Cluster.id == EnterpriseCluster.cluster_id
        ).where(Cluster.slug == cluster)
    else:
        pass

    # 本库是浙江产业带库，但为了对比收录了省外产区（广东汕头/佛山、福建晋江）。
    # 给一个显式开关，免得用户把对比样本当成浙江企业统计。
    if scope == "zhejiang":
        stmt = stmt.where(Enterprise.region_source != "out_of_province")
    elif scope == "out_of_province":
        stmt = stmt.where(Enterprise.region_source == "out_of_province")

    if has_contact:
        stmt = stmt.where(
            or_(
                Enterprise.website != "",
                Enterprise.public_phone != "",
                Enterprise.public_email != "",
                Enterprise.address != "",
            )
        )

    if contact_status:
        stmt = stmt.where(Enterprise.contact_status == contact_status)

    total = session.scalar(select(func.count()).select_from(stmt.subquery())) or 0

    order = SORTS.get(sort)
    rows = session.scalars(stmt.distinct()).all()
    if sort == "completeness":
        rows.sort(key=lambda e: (-completeness(e), e.name))
    elif order is not None and sort in ("name", "name_desc"):
        rows.sort(key=lambda e: e.name, reverse=sort == "name_desc")
    else:
        rows.sort(key=lambda e: (-completeness(e), e.name))

    start = (page - 1) * page_size
    window = rows[start: start + page_size]
    names = enterprise_cluster_names(session, [e.id for e in window])
    flags = pending_attribution(session, [e.id for e in window])
    return EnterprisePage(
        items=[
            to_enterprise_summary(e, names.get(e.id, []), flags.get(e.id, ""))
            for e in window
        ],
        meta=PageMeta(**paginate(total, page, page_size)),
    )


@router.get("/{enterprise_id}", response_model=EnterpriseDetail)
def get_enterprise(enterprise_id: int, session: Session = Depends(get_session)):

    enterprise = session.get(Enterprise, enterprise_id)
    if enterprise is None:
        raise HTTPException(status_code=404, detail="企业不存在")

    links = session.execute(
        select(Cluster, EnterpriseCluster.is_primary)
        .join(EnterpriseCluster, EnterpriseCluster.cluster_id == Cluster.id)
        .where(EnterpriseCluster.enterprise_id == enterprise_id)
        .order_by(EnterpriseCluster.is_primary.desc())
    ).all()

    summary = to_enterprise_summary(enterprise, [c.name for c, _ in links], ATTRIBUTION_NOTES.get(
        next((link.attribution for link in enterprise.cluster_links), "product"), ""
    ))
    return EnterpriseDetail(
        **summary.model_dump(),
        name_en=enterprise.name_en,
        honors=enterprise.honors or [],
        source_url=enterprise.source_url,
        verified_at=enterprise.verified_at,
        enrich_status=enterprise.enrich_status,
        manual_note=enterprise.manual_note,
        clusters=[
            {
                "slug": cluster.slug,
                "name": cluster.name,
                "tier": cluster.tier,
                "data_grade": cluster.data_grade,
                "data_grade_label": grade_label(cluster.data_grade),
                "is_primary": is_primary,
            }
            for cluster, is_primary in links
        ],
        completeness=completeness(enterprise),
    )