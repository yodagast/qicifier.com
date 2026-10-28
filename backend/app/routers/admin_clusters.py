"""``/api/admin/clusters`` — 产业带（集群）档案的编辑接口（默认关闭）。

管理员在这里改的是**本站维护的那部分**：产区简介、别名、规模指标、荣誉、核实程度。
名字、档次、地理归属**不可编辑** —— 那些来自官方名单，改了就等于伪造认定依据。

集群**整体**的核实程度不在这里改：那个值来自名单，改它等于把「官方认定」的标签
用在站方自己写的文字上。可编辑的核实程度只针对简介内容（``summary_grade``）。

写入 ``data/curated/cluster_edits.json``（读—改—原子替换），由灌库流程以最高优先级叠加，
页面照常从数据库读。所以保存之后必须重新灌库才生效 —— 接口在响应里把这句话写清楚，
并且**只重建数据层**：账号与收藏不受影响。

校验规则见 ``services/cluster_edits.py``：规模数字必须带口径与年份，荣誉必须写授予主体，
核实程度只能是既定五档。任何一条不合法都整体拒绝（部分写入比不写入更糟）。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..database import get_session
from ..models import Cluster
from ..services.admin_auth import require_admin
from ..services.cluster_edits import (
    EDITS_FILE,
    EDITABLE_FIELDS,
    delete_edit,
    edit_for,
    edit_status,
    editable_snapshot,
    upsert_edit,
)

router = APIRouter(prefix="/api/admin/clusters", tags=["admin"])

NEXT_STEP = "重新灌库后生效：cd backend && ../.venv/bin/python -m app.cli seed --drop（只重建数据层，账号与收藏保留）"


class MetricIn(BaseModel):
    value: float | int | str
    unit: str
    caliber: str
    year: str
    source_name: str
    source_grade: str = "site_curated"


class HonorIn(BaseModel):
    name: str
    granted_by: str
    year: str = ""
    grade: str = "site_curated"


class ClusterEditIn(BaseModel):
    """字段级 PATCH：只改传上来的字段，没传的保持原样（传空列表才是清空）。"""

    summary: str | None = None
    aliases: list[str] | None = None
    metrics: list[MetricIn] | None = None
    honors: list[HonorIn] | None = None
    summary_grade: str | None = None
    note: str | None = None

    model_config = {"extra": "forbid"}


def _cluster(session: Session, slug: str) -> Cluster:
    cluster = session.scalar(select(Cluster).where(Cluster.slug == slug))
    if cluster is None:
        raise HTTPException(status_code=404, detail=f"集群不存在：{slug}")
    return cluster


@router.get("")
def list_clusters(session: Session = Depends(get_session), _: None = Depends(require_admin)):
    rows = edit_status(session)
    return {
        "total": len(rows),
        "edited": sum(1 for row in rows if row["has_edit"]),
        "items": rows,
        "editable_fields": list(EDITABLE_FIELDS),
        "file": f"data/curated/{EDITS_FILE}",
        "note": NEXT_STEP,
    }


@router.get("/{slug}")
def get_cluster(slug: str, session: Session = Depends(get_session), _: None = Depends(require_admin)):
    """当前**生效**的值（数据库里的），供编辑表单预填。"""
    cluster = _cluster(session, slug)
    return {
        "cluster": editable_snapshot(cluster),
        "edit": edit_for(slug),
        "note": NEXT_STEP,
    }


@router.patch("/{slug}")
def patch_cluster(
    slug: str, payload: ClusterEditIn, session: Session = Depends(get_session), _: None = Depends(require_admin)
):
    _cluster(session, slug)  # 集群必须存在，否则编辑会变成一条永远叠加不上的孤儿记录
    # exclude_unset：只把**传上来的**字段写进叠加层，未提交的字段保持原样
    fields = payload.model_dump(exclude_unset=True)
    if not fields:
        raise HTTPException(status_code=422, detail=f"没有提交任何字段（可编辑：{'、'.join(EDITABLE_FIELDS)}）")
    try:
        record = upsert_edit(slug, fields)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"saved": True, "slug": slug, "edit": record, "note": NEXT_STEP}


@router.delete("/{slug}")
def revert_cluster(slug: str, _: None = Depends(require_admin)):
    """撤销编辑，回退到名单/调研叠加给出的值。"""
    removed = delete_edit(slug)
    if not removed:
        raise HTTPException(status_code=404, detail="该集群没有站方编辑记录")
    return {"reverted": True, "slug": slug, "note": NEXT_STEP}
