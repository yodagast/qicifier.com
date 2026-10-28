"""``/api/tracks`` — 415X 赛道入口（首页与产业地图用）."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..database import get_session
from ..models import Cluster, EnterpriseCluster
from ..schemas import TrackOut

router = APIRouter(prefix="/api/tracks", tags=["tracks"])


@router.get("", response_model=list[TrackOut])
def list_tracks(session: Session = Depends(get_session)):
    rows = session.scalars(
        select(Cluster).where(Cluster.slug.like("415X-%")).order_by(Cluster.name)
    ).all()
    counts = dict(
        session.execute(
            select(EnterpriseCluster.cluster_id, func.count(func.distinct(EnterpriseCluster.enterprise_id)))
            .where(EnterpriseCluster.cluster_id.in_([c.id for c in rows]))
            .group_by(EnterpriseCluster.cluster_id)
        ).all()
    )
    return [
        TrackOut(
            slug=cluster.slug,
            name=cluster.name,
            sector=cluster.sector,
            cluster_count=1,
            enterprise_count=counts.get(cluster.id, 0),
        )
        for cluster in rows
    ]