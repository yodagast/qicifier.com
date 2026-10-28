"""``/api/admin`` — 人工补充与数据质量工具（默认关闭）。

手工补充是企业库的兜底路径：自动发现核实不到官网的企业，由维护者手工填写公开信息。

安全设计：

* 默认**关闭**。只有设置了 ``ADMIN_TOKEN`` 环境变量后才启用；请求须带
  ``X-Admin-Token`` 头（校验见 ``services/admin_auth.py``）。未启用时路由返回 404。
* 写入 ``data/curated/manual_sites.json``，采用「读—改—原子替换」，避免写坏文件。
* 强校验：企业名必须在官方名单内、电话不得是个人手机号、官网必须 http(s)、
  邮箱必须合法。校验失败返回 422 并逐条说明原因。
* 不允许删除官方名单里的企业——只能补充/更正公开联系方式。
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..config import settings
from ..database import get_session
from ..models import Enterprise
from ..services.admin_auth import require_admin
from ..services.contacts import EMAIL_RE, MOBILE_RE, load_contacts

router = APIRouter(prefix="/api/admin", tags=["admin"])

MANUAL_FILE = settings.curated_dir / "manual_sites.json"


class ContactUpsert(BaseModel):
    name: str = Field(..., min_length=2, max_length=160)
    website: str = ""
    public_phone: str = ""
    public_email: str = ""
    address: str = ""
    source_url: str = ""
    note: str = ""


def _read_manual() -> dict:
    if not MANUAL_FILE.exists():
        return {"_note": "手工补充的企业公开信息（由 /api/admin/contacts 维护）", "companies": []}
    try:
        return json.loads(MANUAL_FILE.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=500, detail=f"manual_sites.json 解析失败：{exc}") from exc


def _write_manual(payload: dict) -> None:
    MANUAL_FILE.parent.mkdir(parents=True, exist_ok=True)
    handle = tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=MANUAL_FILE.parent, delete=False, suffix=".tmp"
    )
    try:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
        handle.flush()
        os.fsync(handle.fileno())
    finally:
        handle.close()
    Path(handle.name).replace(MANUAL_FILE)


def validate(entry: ContactUpsert, known_names: set[str]) -> list[str]:
    problems: list[str] = []
    if entry.name not in known_names:
        problems.append("企业名不在官方名单中（为避免产生无官方依据的记录，仅允许补充名单内企业）")
    if entry.website and not entry.website.startswith(("http://", "https://")):
        problems.append("官网需以 http:// 或 https:// 开头")
    if entry.public_phone and MOBILE_RE.search(entry.public_phone):
        problems.append("电话是个人手机号，本站不采集个人联系方式")
    if entry.public_email and not EMAIL_RE.match(entry.public_email.strip().lower()):
        problems.append("邮箱格式不合法")
    if entry.source_url and not entry.source_url.startswith(("http://", "https://")):
        problems.append("来源链接需以 http:// 或 https:// 开头")
    if not (entry.website or entry.public_phone or entry.public_email or entry.address):
        problems.append("至少填写官网 / 电话 / 邮箱 / 地址之一")
    return problems


@router.get("/contacts/gaps")
def list_gaps(
    session: Session = Depends(get_session),
    _: None = Depends(require_admin),
    contact_status: str = "empty",
    limit: int = 500,
):
    """列出仍缺公开联系方式的企业，供人工补充。"""
    total = session.scalar(
        select(func.count(Enterprise.id)).where(Enterprise.contact_status == contact_status)
    ) or 0
    rows = session.scalars(
        select(Enterprise)
        .where(Enterprise.contact_status == contact_status)
        .order_by(Enterprise.name)
        .limit(max(1, min(limit, 2000)))
    ).all()
    return {
        "total": total,
        "returned": len(rows),
        "items": [
            {
                "name": e.name,
                "city": e.city,
                "district": e.district,
                "list_names": e.list_names or [],
                "website": e.website,
                "public_phone": e.public_phone,
                "public_email": e.public_email,
                "enrich_status": e.enrich_status,
            }
            for e in rows
        ],
    }


@router.get("/contacts")
def list_manual(_: None = Depends(require_admin)):
    payload = _read_manual()
    return {"count": len(payload.get("companies", [])), "companies": payload.get("companies", [])}


@router.post("/contacts", status_code=201)
def upsert_contact(
    entry: ContactUpsert,
    session: Session = Depends(get_session),
    _: None = Depends(require_admin),
):
    """新增或更正一家企业的公开联系方式（写入 manual_sites.json，优先级最高）。"""
    known = {name for (name,) in session.execute(select(Enterprise.name)).all()}
    problems = validate(entry, known)
    if problems:
        raise HTTPException(status_code=422, detail={"problems": problems})

    payload = _read_manual()
    companies = payload.setdefault("companies", [])
    record = entry.model_dump()
    record["public_email"] = record["public_email"].strip().lower()
    record["public_phone"] = record["public_phone"].strip()

    for index, existing in enumerate(companies):
        if existing.get("name") == entry.name:
            merged = {**existing, **{k: v for k, v in record.items() if v != ""}}
            companies[index] = merged
            _write_manual(payload)
            return {"updated": True, "record": merged}
    companies.append(record)
    _write_manual(payload)
    return {"updated": False, "record": record}


@router.delete("/contacts")
def delete_contact(name: str = Query(..., min_length=1), _: None = Depends(require_admin)):
    """撤回一条人工补充（撤销填错的内容）。

    为什么必须有：人工补充的优先级最高，**填错就会盖住自动发现的结果**，
    而在此之前管理界面只能新增、不能撤销 —— 填错一条就只能去手工编辑 JSON。
    一个「优先级最高」的层没有撤销入口，是个迟早会被用到的缺口。

    企业名走**查询参数**而不是路径参数：真实数据里有
    「Herman Miller / Steelcase」这种含斜杠的名字，编码成 ``%2F`` 之后
    Starlette 仍会先解码再切分路径，DELETE 会落到 404 上。
    企业名是**数据**，不是标识符。
    """
    payload = _read_manual()
    companies = payload.get("companies", [])
    remaining = [row for row in companies if row.get("name") != name]
    if len(remaining) == len(companies):
        raise HTTPException(status_code=404, detail=f"没有「{name}」的人工补充记录")
    payload["companies"] = remaining
    _write_manual(payload)
    return {"removed": True, "name": name, "remaining": len(remaining)}


@router.post("/contacts/bulk")
def bulk_upsert(
    entries: list[ContactUpsert],
    session: Session = Depends(get_session),
    _: None = Depends(require_admin),
):
    """批量导入（例如整理好的表格）。逐条校验，全部合法才写入。"""
    known = {name for (name,) in session.execute(select(Enterprise.name)).all()}
    failures = []
    for index, entry in enumerate(entries):
        problems = validate(entry, known)
        if problems:
            failures.append({"index": index, "name": entry.name, "problems": problems})
    if failures:
        raise HTTPException(status_code=422, detail={"failures": failures, "accepted": 0})

    payload = _read_manual()
    companies = payload.setdefault("companies", [])
    by_name = {row.get("name"): index for index, row in enumerate(companies)}
    added = updated = 0
    for entry in entries:
        record = entry.model_dump()
        record["public_email"] = record["public_email"].strip().lower()
        record["public_phone"] = record["public_phone"].strip()
        if entry.name in by_name:
            index = by_name[entry.name]
            companies[index] = {**companies[index], **{k: v for k, v in record.items() if v != ""}}
            updated += 1
        else:
            companies.append(record)
            by_name[entry.name] = len(companies) - 1
            added += 1
    _write_manual(payload)
    return {"added": added, "updated": updated, "total": len(companies)}


@router.get("/stats")
def admin_stats(session: Session = Depends(get_session), _: None = Depends(require_admin)):
    contacts = load_contacts()
    rows = session.execute(select(Enterprise.contact_status, Enterprise.id)).all()
    counts: dict[str, int] = {}
    for status, _id in rows:
        counts[status] = counts.get(status, 0) + 1
    return {
        "enterprise_total": len(rows),
        "by_status": counts,
        "manual_entries": len(_read_manual().get("companies", [])),
        "with_website": sum(1 for r in contacts.values() if r.get("website")),
        "with_phone": sum(1 for r in contacts.values() if r.get("public_phone")),
        "with_email": sum(1 for r in contacts.values() if r.get("public_email")),
    }