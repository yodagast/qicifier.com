"""``/api/regions`` — 四级地理层（省 / 市 / 县 / 有产业的镇街）."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..database import get_session
from ..models import Region
from ..schemas import RegionOut
from ..services.queries import region_rollup

router = APIRouter(prefix="/api/regions", tags=["regions"])


def build_tree(session: Session) -> list[RegionOut]:
    rows = session.scalars(select(Region).order_by(Region.code)).all()
    enterprise_counts, cluster_counts = region_rollup(session, [r.id for r in rows])

    nodes: dict[str, RegionOut] = {}
    for region in rows:
        nodes[region.code] = RegionOut(
            code=region.code,
            name=region.name,
            level=region.level,
            parent_code=region.parent_code,
            has_industry=region.has_industry,
            is_zone=region.code.startswith("Z"),
            enterprise_count=enterprise_counts.get(region.id, 0),
            cluster_count=cluster_counts.get(region.id, 0),
        )

    children: dict[str, list[RegionOut]] = {}
    for region in rows:
        parent_code = region.parent_code or ""
        if parent_code:
            children.setdefault(parent_code, []).append(nodes[region.code])

    # 自下而上汇总：镇街 → 县 → 市 → 省（先挂子节点，再递归累加）
    def attach(node: RegionOut, depth: int = 0) -> None:
        if depth > 6:
            return
        for child in children.get(node.code, []):
            node.children.append(child)
            attach(child, depth + 1)
            node.enterprise_count += child.enterprise_count
            node.cluster_count += child.cluster_count

    roots = [nodes[r.code] for r in rows if r.level == "province"]
    for root in roots:
        attach(root)
    return roots


@router.get("", response_model=list[RegionOut])
def list_regions(session: Session = Depends(get_session)):
    return build_tree(session)


@router.get("/flat", response_model=list[RegionOut])
def flat_regions(session: Session = Depends(get_session)):
    rows = session.scalars(select(Region).order_by(Region.code)).all()
    enterprise_counts, cluster_counts = region_rollup(session, [r.id for r in rows])
    return [
        RegionOut(
            code=r.code,
            name=r.name,
            level=r.level,
            parent_code=r.parent_code,
            has_industry=r.has_industry,
            is_zone=r.code.startswith("Z"),
            enterprise_count=enterprise_counts.get(r.id, 0),
            cluster_count=cluster_counts.get(r.id, 0),
        )
        for r in rows
    ]


@router.get("/{code}", response_model=RegionOut)
def get_region(code: str, session: Session = Depends(get_session)):
    region = session.scalar(select(Region).where(Region.code == code))
    if region is None:
        raise HTTPException(status_code=404, detail="行政区不存在")
    enterprise_counts, cluster_counts = region_rollup(session, [region.id])
    children = session.scalars(select(Region).where(Region.parent_code == code)).all()
    return RegionOut(
        code=region.code,
        name=region.name,
        level=region.level,
        parent_code=region.parent_code,
        has_industry=region.has_industry,
        is_zone=region.code.startswith("Z"),
        enterprise_count=enterprise_counts.get(region.id, 0),
        cluster_count=cluster_counts.get(region.id, 0),
        children=[
            RegionOut(
                code=c.code,
                name=c.name,
                level=c.level,
                parent_code=c.parent_code,
                has_industry=c.has_industry,
                is_zone=c.code.startswith("Z"),
                enterprise_count=enterprise_counts.get(c.id, 0),
                cluster_count=cluster_counts.get(c.id, 0),
            )
            for c in children
        ],
    )