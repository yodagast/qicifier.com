"""Shared query helpers for the API routers."""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..models import Cluster, ClusterRegion, Enterprise, EnterpriseCluster, Region
from ..schemas import GRADE_LABELS, EnterpriseSummary


def grade_label(value: str) -> str:
    return GRADE_LABELS.get(value, value)


# 归属依据 → 页面上那句话。``product`` 是按主营产品推出来的，不需要解释；
# 另外两种都必须说明「这个归属是怎么来的」。
ATTRIBUTION_NOTES = {
    "product": "",
    "district_fallback": "归属待确认：按注册地所在镇街的主导集群归入，请在集群页核对",
    # 产区调研清单自带归属依据，不是我们猜的，但也不是官方认定 —— 说明里两件事都要讲
    "research_list": "归属依据：产区调研清单（第三方调研，非官方认定）",
}


def to_enterprise_summary(
    enterprise: Enterprise, cluster_names: list[str], attribution_note: str = ""
) -> EnterpriseSummary:
    """企业 → 列表用的摘要。**这是唯一的实现。**

    别在别处再拼一个 `EnterpriseSummary(...)`：漏字段是静默的 —— 新增
    `website_kind` / `verify_tier` 这类字段时，另一份副本不会报错，只会让
    某个页面（收藏列表）少显示一块内容，而且没人会立刻发现。
    """
    return EnterpriseSummary(
        id=enterprise.id,
        name=enterprise.name,
        city=enterprise.city,
        district=enterprise.district,
        town=enterprise.town,
        region_source=enterprise.region_source,
        summary=enterprise.summary,
        products=enterprise.products or [],
        founded_year=enterprise.founded_year,
        registered_capital=enterprise.registered_capital,
        verify_tier=enterprise.verify_tier,
        business_model=enterprise.business_model,
        address_registered=enterprise.address_registered,
        website=enterprise.website,
        website_kind=enterprise.website_kind,
        public_phone=enterprise.public_phone,
        phone_nature=enterprise.phone_nature,
        address_type=enterprise.address_type,
        public_email=enterprise.public_email,
        address=enterprise.address,
        contact_status=enterprise.contact_status,
        contact_sources=enterprise.contact_sources or {},
        data_grade=enterprise.data_grade,
        data_grade_label=grade_label(enterprise.data_grade),
        list_names=enterprise.list_names or [],
        cluster_names=cluster_names,
        attribution_note=attribution_note,
    )


def enterprise_cluster_names(session: Session, enterprise_ids: list[int]) -> dict[int, list[str]]:
    """cluster names keyed by enterprise id, primary cluster first."""
    if not enterprise_ids:
        return {}
    rows = session.execute(
        select(EnterpriseCluster.enterprise_id, Cluster.name, EnterpriseCluster.is_primary)
        .join(Cluster, Cluster.id == EnterpriseCluster.cluster_id)
        .where(EnterpriseCluster.enterprise_id.in_(enterprise_ids))
        .order_by(EnterpriseCluster.enterprise_id, EnterpriseCluster.is_primary.desc())
    ).all()
    result: dict[int, list[str]] = {}
    for enterprise_id, name, _ in rows:
        result.setdefault(enterprise_id, []).append(name)
    return result


def locate_region(regions: list[Region], cities: dict[str, Region]) -> tuple[str, str, str]:
    """Resolve 市 / 县 / 镇 for a cluster from its linked regions."""
    city = district = town = ""
    for region in regions:
        if region.level == "town":
            town = town or region.name
            parent = cities.get(region.parent_code or "")
            if parent:
                district = district or parent.name
                grandparent = cities.get(parent.parent_code or "")
                city = city or (grandparent.name if grandparent else "")
        elif region.level == "district":
            district = district or region.name
            parent = cities.get(region.parent_code or "")
            city = city or (parent.name if parent else "")
        elif region.level == "city":
            # 市级归口的产区（成员散在好几个县区，产区名写的是市）只挂到市一级。
            city = city or region.name
    return city, district, town


def cluster_region_map(session: Session, cluster_ids: list[int]) -> dict[int, list[Region]]:
    if not cluster_ids:
        return {}
    rows = session.execute(
        select(ClusterRegion.cluster_id, Region)
        .join(Region, Region.id == ClusterRegion.region_id)
        .where(ClusterRegion.cluster_id.in_(cluster_ids))
        .order_by(ClusterRegion.is_primary.desc())
    ).all()
    result: dict[int, list[Region]] = {}
    for cluster_id, region in rows:
        result.setdefault(cluster_id, []).append(region)
    return result


def enterprise_counts_by_cluster(session: Session, cluster_ids: list[int]) -> dict[int, int]:
    if not cluster_ids:
        return {}
    rows = session.execute(
        select(EnterpriseCluster.cluster_id, func.count(EnterpriseCluster.enterprise_id))
        .where(EnterpriseCluster.cluster_id.in_(cluster_ids))
        .group_by(EnterpriseCluster.cluster_id)
    ).all()
    return {cluster_id: count for cluster_id, count in rows}


def pending_attribution(session: Session, enterprise_ids: list[int]) -> dict[int, str]:
    """Enterprises whose cluster link needs confirmation (调研方案 §04 兜底规则)."""
    if not enterprise_ids:
        return {}
    rows = session.execute(
        select(EnterpriseCluster.enterprise_id)
        .where(
            EnterpriseCluster.enterprise_id.in_(enterprise_ids),
            EnterpriseCluster.needs_confirmation.is_(True),
        )
        .distinct()
    ).all()
    return {enterprise_id: "归属待确认" for (enterprise_id,) in rows}


def region_rollup(session: Session, region_ids: list[int]) -> tuple[dict[int, int], dict[int, int]]:
    """(enterprise_count, cluster_count) per region for the requested ids."""
    if not region_ids:
        return {}, {}
    enterprise_rows = session.execute(
        select(Enterprise.region_id, func.count(Enterprise.id))
        .where(Enterprise.region_id.in_(region_ids))
        .group_by(Enterprise.region_id)
    ).all()
    cluster_rows = session.execute(
        select(ClusterRegion.region_id, func.count(ClusterRegion.cluster_id))
        .where(ClusterRegion.region_id.in_(region_ids))
        .group_by(ClusterRegion.region_id)
    ).all()
    return (
        {region_id: count for region_id, count in enterprise_rows},
        {region_id: count for region_id, count in cluster_rows},
    )


def paginate(total: int, page: int, page_size: int) -> dict:
    pages = (total + page_size - 1) // page_size if page_size else 0
    return {"total": total, "page": page, "page_size": page_size, "pages": pages}