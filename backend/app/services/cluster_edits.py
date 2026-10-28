"""站方维护的**产业带档案**（集群简介 / 别名 / 规模指标 / 荣誉）。

为什么需要这一层：集群页面上的文字与数字，一部分来自官方名单，一部分来自第三方调研，
还有一部分（尤其是 47 个镇街级块状经济）**只有本站有**。以前这最后一部分没有编辑入口 ——
要改一句简介就得手工去动 `research_clusters.json` 再整库重灌，而重灌在修好之前
会把用户账号一起删掉。于是「编辑产业带」变成了一个危险动作。

这里的做法与「人工补充联系方式」一致：**写一个独立的叠加文件**，
由灌库流程以**最高优先级**叠加到集群上，页面照常从数据库读：

    data/curated/cluster_edits.json     ← 管理界面 / 接口写入
    seed.loader.apply_cluster_edits()   ← 灌库时叠加（官方名单 → 调研叠加 → 本站编辑）

三条不可让步的规则：

1. **规模数字必须带口径与年份。** 前端在没有带口径的数据时明确不显示规模值，
   接口这一层也必须拒绝裸数字 —— 否则「不显示无口径数字」只是句口号。
2. **荣誉必须写授予主体。** 没有授予方的称号无法核实，页面也就无法标出核实程度。
3. **核实程度是必填的**，且只能是既定的五档：站方写的内容默认 `site_curated`，
   不许悄悄套用「官方认定」。
"""

from __future__ import annotations

import json
import os
import re
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import settings
from ..models import Cluster, Region
from ..schemas import GRADE_LABELS
from .queries import cluster_region_map, locate_region

EDITS_FILE = "cluster_edits.json"

# 可以编辑的字段。名字、档次、地理归属、以及**集群整体的核实程度**都不在其中：
# 那些来自官方名单，改了就等于伪造认定依据（要改它们得先改名单）。
#
# 注意 `summary_grade` 与 `cluster.data_grade` 是两件事：前者说「这段简介是谁写的」，
# 后者说「这个集群的档案来自哪份名单」。站方补写一段简介，不该把官方认定的集群降级，
# 也不该把一段没人签名的文字说成官方表述。
EDITABLE_FIELDS = ("summary", "summary_grade", "aliases", "honors", "metrics", "note")

GRADES = tuple(GRADE_LABELS)
MAX_SUMMARY = 2000
MAX_NOTE = 500
MAX_ALIASES = 20
MAX_ITEMS = 60


def _now() -> str:
    return datetime.now(timezone(timedelta(hours=8))).isoformat(timespec="seconds")


def edits_path() -> Path:
    return settings.curated_dir / EDITS_FILE


def read_edits() -> dict:
    path = edits_path()
    if not path.exists():
        return {"_note": "站方在管理界面维护的产业带字段（灌库时以最高优先级叠加）", "edits": []}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {"_note": "（编辑文件解析失败，已忽略）", "edits": []}
    payload.setdefault("edits", [])
    return payload


def write_atomic(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False, suffix=".tmp")
    try:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    finally:
        handle.close()
    Path(handle.name).replace(path)


def edit_for(slug: str) -> dict | None:
    return next((row for row in read_edits()["edits"] if row.get("slug") == slug), None)


# ------------------------------------------------------------------ 校验
def _text(value, field: str, problems: list[str], limit: int, required: bool = False) -> str:
    if value is None:
        if required:
            problems.append(f"{field} 不能为空")
        return ""
    if not isinstance(value, str):
        problems.append(f"{field} 必须是字符串")
        return ""
    value = value.strip()
    if required and not value:
        problems.append(f"{field} 不能为空")
    if len(value) > limit:
        problems.append(f"{field} 超过 {limit} 字")
    return value


def validate_edit(payload: dict) -> tuple[dict, list[str]]:
    """→ ``(清洗后的值, 问题列表)``。有任何问题都不落盘。"""
    problems: list[str] = []
    clean: dict = {}
    fields = set(payload)

    if "summary" in fields:
        clean["summary"] = _text(payload["summary"], "产区简介", problems, MAX_SUMMARY)
    if "note" in fields:
        clean["note"] = _text(payload["note"], "编辑说明", problems, MAX_NOTE)
    if "summary_grade" in fields:
        grade = (payload["summary_grade"] or "").strip()
        if grade not in GRADES:
            problems.append(f"简介的核实程度只能是：{'、'.join(GRADES)}")
        else:
            clean["summary_grade"] = grade

    if "aliases" in fields:
        raw = payload["aliases"] or []
        if not isinstance(raw, list):
            problems.append("别名必须是列表")
        else:
            aliases = [str(a).strip() for a in raw if str(a).strip()]
            if len(aliases) > MAX_ALIASES:
                problems.append(f"别名最多 {MAX_ALIASES} 个")
            clean["aliases"] = sorted(dict.fromkeys(aliases))

    if "metrics" in fields:
        metrics = payload["metrics"] or []
        if not isinstance(metrics, list):
            problems.append("规模指标必须是列表")
        elif len(metrics) > MAX_ITEMS:
            problems.append(f"规模指标最多 {MAX_ITEMS} 条")
        else:
            cleaned = []
            for index, item in enumerate(metrics, 1):
                if not isinstance(item, dict):
                    problems.append(f"第 {index} 条指标格式不对")
                    continue
                entry = {
                    "value": item.get("value"),
                    "unit": _text(item.get("unit"), f"第 {index} 条指标的单位", problems, 16, required=True),
                    # 口径与年份是硬要求：没有口径的数字前端不会显示，接口也不该收
                    "caliber": _text(item.get("caliber"), f"第 {index} 条指标的口径", problems, 80, required=True),
                    "year": _text(item.get("year"), f"第 {index} 条指标的年份", problems, 16, required=True),
                    "source_name": _text(item.get("source_name"), f"第 {index} 条指标的来源", problems, 160, required=True),
                    "source_grade": (item.get("source_grade") or "site_curated").strip(),
                }
                if entry["value"] in (None, ""):
                    problems.append(f"第 {index} 条指标缺数值")
                if entry["source_grade"] not in GRADES:
                    problems.append(f"第 {index} 条指标的来源核实程度不合法")
                cleaned.append(entry)
            clean["metrics"] = cleaned

    if "honors" in fields:
        honors = payload["honors"] or []
        if not isinstance(honors, list):
            problems.append("荣誉必须是列表")
        elif len(honors) > MAX_ITEMS:
            problems.append(f"荣誉最多 {MAX_ITEMS} 条")
        else:
            cleaned = []
            for index, item in enumerate(honors, 1):
                if not isinstance(item, dict):
                    problems.append(f"第 {index} 条荣誉格式不对")
                    continue
                entry = {
                    "name": _text(item.get("name"), f"第 {index} 条荣誉的名称", problems, 120, required=True),
                    # 没有授予主体的称号无法核实，页面也就无法标出核实程度
                    "granted_by": _text(item.get("granted_by"), f"第 {index} 条荣誉的授予方", problems, 160, required=True),
                    "year": _text(item.get("year"), f"第 {index} 条荣誉的年份", problems, 16),
                    "grade": (item.get("grade") or "site_curated").strip(),
                }
                if entry["grade"] not in GRADES:
                    problems.append(f"第 {index} 条荣誉的核实程度不合法")
                cleaned.append(entry)
            clean["honors"] = cleaned

    if not fields & set(EDITABLE_FIELDS):
        problems.append(f"没有可编辑的字段（可编辑：{'、'.join(EDITABLE_FIELDS)}）")
    return clean, problems


# ------------------------------------------------------------------ 写入
def upsert_edit(slug: str, payload: dict) -> dict:
    """把一次编辑并进叠加文件（同 slug 只保留一条，字段级覆盖）。"""
    clean, problems = validate_edit(payload)
    if problems:
        raise ValueError("；".join(problems))

    data = read_edits()
    rows = [row for row in data["edits"] if row.get("slug") != slug]
    existing = next((row for row in data["edits"] if row.get("slug") == slug), {})
    record = {**existing, **clean, "slug": slug, "updated_at": _now()}
    rows.append(record)
    rows.sort(key=lambda row: row["slug"])
    data["edits"] = rows
    data["_note"] = "站方在管理界面维护的产业带字段（灌库时以最高优先级叠加）"
    write_atomic(edits_path(), data)
    return record


def delete_edit(slug: str) -> bool:
    data = read_edits()
    rows = [row for row in data["edits"] if row.get("slug") != slug]
    if len(rows) == len(data["edits"]):
        return False
    data["edits"] = rows
    write_atomic(edits_path(), data)
    return True


# ------------------------------------------------------------------ 叠加
def apply_cluster_edits(session: Session, clusters: dict[str, Cluster]) -> int:
    """灌库时把编辑叠加到集群上（优先级最高：官方名单 → 调研叠加 → 本站编辑）。"""
    applied = 0
    for row in read_edits()["edits"]:
        cluster = clusters.get(row.get("slug", ""))
        if cluster is None:
            print(f"  warn: 产业带编辑指向不存在的集群：{row.get('slug')}")
            continue
        if "summary" in row:
            cluster.summary = row["summary"]
        if row.get("summary_grade"):
            cluster.summary_grade = row["summary_grade"]
        if row.get("aliases"):
            cluster.aliases = sorted(set(cluster.aliases or []) | set(row["aliases"]))
        if "metrics" in row:
            cluster.metrics = row["metrics"]
        if "honors" in row:
            cluster.honors = row["honors"]
        # 集群自己的核实程度**不在这里改**：它来自名单（见 EDITABLE_FIELDS 的注释）
        cluster.verified_at = _parse_time(row.get("updated_at"))
        cluster.sources = [
            {"name": "站方维护的产业带档案（管理界面）", "grade": row.get("summary_grade") or "site_curated"},
            *[s for s in (cluster.sources or []) if not s.get("name", "").startswith("站方维护的产业带档案")],
        ]
        applied += 1
    session.flush()
    return applied


def _parse_time(value: str | None):
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def edit_status(session: Session) -> list[dict]:
    """管理界面用：所有集群 + 是否已被编辑。"""
    rows = session.scalars(select(Cluster).order_by(Cluster.data_grade, Cluster.name)).all()
    by_slug = {row["slug"]: row for row in read_edits()["edits"]}
    # 集群本身不带市/县：那是从 cluster_region 反推出来的（与集群列表页同一个函数）
    region_map = cluster_region_map(session, [c.id for c in rows])
    cities = {r.code: r for r in session.scalars(select(Region)).all()}
    located = {c.id: locate_region(region_map.get(c.id, []), cities) for c in rows}
    return [
        {
            "slug": cluster.slug,
            "name": cluster.name,
            "data_grade": cluster.data_grade,
            "scale_tier": cluster.scale_tier,
            "tier": cluster.tier,
            "city": located[cluster.id][0],
            "district": located[cluster.id][1],
            "enterprise_count": len(cluster.enterprise_links or []),
            "has_edit": cluster.slug in by_slug,
            "edited_at": by_slug.get(cluster.slug, {}).get("updated_at", ""),
            "edited_fields": sorted(
                key for key in EDITABLE_FIELDS if key in by_slug.get(cluster.slug, {})
            ),
            "metric_count": len(cluster.metrics or []),
            "honor_count": len(cluster.honors or []),
        }
        for cluster in rows
    ]


def editable_snapshot(cluster: Cluster) -> dict:
    """当前**生效**的值（数据库里的），供编辑表单预填 —— 不是叠加文件里的原始值。

    编辑界面要看到的是页面上真正显示的东西，否则「改了一个数字却没变化」
    会让人以为是保存失败（实际是被别的叠加层盖住了）。
    """
    return {
        "slug": cluster.slug,
        "name": cluster.name,
        "summary": cluster.summary or "",
        "aliases": list(cluster.aliases or []),
        "metrics": list(cluster.metrics or []),
        "honors": list(cluster.honors or []),
        "data_grade": cluster.data_grade,
        "summary_grade": cluster.summary_grade or "",
        "sources": cluster.sources or [],
        "regions": [{"name": r.region.name, "role": r.role} for r in (cluster.regions or [])],
        "enterprise_count": len(cluster.enterprise_links or []),
    }


def is_valid_slug(slug: str) -> bool:
    return bool(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,95}", slug or ""))
