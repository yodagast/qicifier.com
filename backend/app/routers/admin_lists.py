"""``/api/admin/lists`` — 名单 CSV 的批量导入（默认关闭，见 ``services/admin_auth.py``）。

存在的理由：企业库的骨架来自一份份外部分档清单，而这些清单**会不断修订**
（``..._副本.csv`` 就是同一份名单的第二版）。每次修订都改一遍解析脚本，
既慢又容易漏掉新出现的哨兵值。所以把「解析一份名单 CSV」做成接口。

一次导入发生三件事（解析规则全部在 ``services/list_import.py``）：

1. 逐行解析 → ``data/source/<source_key>.json``（证据层，保留原文 raw）
2. 生成联系方式层 → ``data/curated/<source_key>_list.json``
3. 生成候选官网层 → ``data/curated/<source_key>_sites.json``（必须过验收门才成为「已核实」）
4. 登记到 ``data/curated/imported_lists.json``：下游据此**自动**发现这份名单，
   不需要在代码里再改 ``SOURCE_FILES`` / 联系方式分层 / 来源声明

**同一 source_key 再导入 = 覆盖该名单**（修订版把增删一并生效，不是往旧数据里追加）。

接口不碰数据库：产物文件才是数据源，导入后需重新灌库。
接口会先在**库外**完成全部校验，任何一行不合规都不会落盘 —— 半个名单比旧名单更糟。
"""

from __future__ import annotations

import json

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import settings
from ..database import get_session
from ..models import Enterprise
from ..seed.companies import load_company_skeletons, resolve_cluster_slug
from ..services.admin_auth import require_admin
from ..services.list_import import GRADE_PREFIX, import_list, imported_lists

router = APIRouter(prefix="/api/admin/lists", tags=["admin"])

MAX_UPLOAD_BYTES = 8 * 1024 * 1024

NEXT_STEP = "已重新灌库，页面立即生效（只重建数据层，账号与收藏保留）"


@router.get("")
def list_imported(_: None = Depends(require_admin)):
    """已导入的名单（注册表内容）与它们对应的产物文件。"""
    return {
        "lists": imported_lists(),
        "grades": sorted(GRADE_PREFIX),
        "hint": "同一 source_key 再导入即覆盖该名单；导入后需重新灌库才会生效",
    }


@router.post("/reseed")
def reseed_data(_: None = Depends(require_admin)):
    """按 ``data/`` 下的清单重建数据层（region / cluster / enterprise）。

    **为什么要有这个接口**：导入名单、保存人工补充、改产业带档案，这三件事
    都只写 ``data/`` 下的产物文件 —— 接口刻意不碰数据库（产物文件才是数据源）。
    但「不碰数据库」对外就成了没做完：用户导完一份 CSV，页面纹丝不动，
    而生效方式是回终端敲 ``python -m app.cli seed --drop``。命令抄错一个字
    就是一次失败的灌库，且报错只有终端能看到。

    所以把 CLI 里那条**同一条**路径（``cli.reseed``）暴露成一个按钮。
    刻意不做成「导入后自动灌库」：灌库要删重建整张 enterprise 表，几秒钟里
    所有查询都可能落空 —— 这个代价该由调用方按自己的节奏决定，而不是被导入顺带触发。

    账号与收藏不受影响，见 ``cli.droppable_tables``。返回灌库后的各表计数，
    让调用方看到「确实生效了」，而不是只能靠页面刷出来判断。
    """
    from ..cli import reseed as _reseed

    try:
        counts = _reseed()
    except Exception as exc:  # pragma: no cover - 运维故障，报告原文
        raise HTTPException(status_code=500, detail=f"灌库失败：{exc}") from exc
    return {"ok": True, "counts": counts, "next_step": NEXT_STEP}


@router.post("/import")
async def import_csv(
    file: UploadFile = File(..., description="名单 CSV（UTF-8，可带 BOM）"),
    list_name: str = Form(..., description="名单全名，会写进来源声明与页面标签"),
    source_key: str = Form(..., description="短键，决定产物文件名；同名再导入即覆盖"),
    grade: str = Form("site_curated", description="核实程度：official / site_curated / …"),
    source_file: str = Form("", description="原始文件名或路径，写进来源声明备查"),
    used_for: str = Form("", description="这份名单在库里做什么（写进来源声明的说明）"),
    role: str = Form(
        "",
        description="来源地图上的一句话角色（如「产区企业层」「企业骨架」）；留空则来源地图不显示这一行",
    ),
    default_cluster_slug: str = Form(
        "",
        description="整份清单都属于某个集群时填它的 slug（如 415X-现代纺织与服装）；"
        "逐行自带的归属优先于它。留空 = 按行决定。",
    ),
    dry_run: bool = Form(False, description="只解析并回报告，不落盘"),
    session: Session = Depends(get_session),
    _: None = Depends(require_admin),
):
    raw = await file.read()
    if not raw:
        raise HTTPException(status_code=422, detail="上传内容为空")
    if len(raw) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail=f"文件超过 {MAX_UPLOAD_BYTES // 1024 // 1024}MB")
    # 整份归属指向一个不存在的集群时，几百行的归属会**静默挂空** ——
    # 页面只是少了集群归属，不报错。所以这里先拦住，拼错一个字母立即 422。
    slug = default_cluster_slug.strip()
    if slug and slug not in _known_cluster_slugs():
        raise HTTPException(
            status_code=422,
            detail=f"default_cluster_slug「{slug}」不是已建档的集群 slug（留空 = 按行决定归属）",
        )
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise HTTPException(status_code=422, detail=f"请用 UTF-8 编码：{exc}") from exc

    try:
        result = import_list(
            text=text,
            list_name=list_name.strip(),
            grade=grade.strip(),
            source_key=source_key.strip(),
            source_file=(source_file or file.filename or "").strip(),
            used_for=used_for,
            role=role,
            default_cluster_slug=default_cluster_slug.strip(),
            dry_run=dry_run,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    report = result["report"]
    report["cluster_mapping"] = _cluster_mapping(result["parsed"]["rows"])
    report["database"] = _database_delta(
        session, result["parsed"]["rows"], dry_run, source_key.strip()
    )
    return report


def _known_cluster_slugs() -> set[str]:
    """骨架 + 调研派生的集群 slug。导入报告用它判「整份归属指向的集群存在吗」。

    三个来源都要读：415X 赛道在 ``tracks`` 下、镇街级产区和调研派生产区在
    ``clusters`` 下、22 个「新星」产业群在官方名单里（灌库时才建，不在骨架文件里）。
    """
    slugs: set[str] = set()
    skeleton = settings.curated_dir / "clusters_seed.json"
    if skeleton.exists():
        payload = json.loads(skeleton.read_text(encoding="utf-8"))
        for group in ("tracks", "clusters"):
            for row in payload.get(group, []):
                if row.get("slug"):
                    slugs.add(row["slug"])
    derived = settings.curated_dir / "research_clusters.json"
    if derived.exists():
        payload = json.loads(derived.read_text(encoding="utf-8"))
        for row in payload.get("clusters", []):
            if row.get("slug"):
                slugs.add(row["slug"])
    # 「新星」产业群是按官方名单在灌库时建出来的，slug 得从那份名单算
    # （与 seed.companies.newstar_slug_index 同一规则）。
    newstar = settings.source_dir / "zhejiang_clusters_batch4.json"
    if newstar.exists():
        payload = json.loads(newstar.read_text(encoding="utf-8"))
        for row in payload.get("新星产业群", []):
            slugs.add(f"newstar-{row['seq']:02d}-{row['cluster']}")
    return slugs


def _cluster_mapping(rows: list[dict]) -> dict:
    """这批行能落到哪些已有集群 —— 落不上、以及**按什么依据**落的，都要能看见。

    映射规则与灌库时**是同一个函数**：解析阶段算好的 ``list_cluster_slug``
    （块状经济 / 产业带 / 产区 / 集群类型列）优先，其次是
    ``seed.companies.resolve_cluster_slug`` 的兜底（赛道、整份归属）。
    导入报告里算出来的结果，就是灌库后的结果。

    **按行进总数，不按分组取最后一行。** 两处都必须按行：
    落位与否取决于这一行的县市区（「中医药大健康」在兰溪/天台能落到新星产业群，
    在滨江就不该落），把分组当成整体、用最后一行的 slug 代表全组，
    曾让 21 个赛道全部显示「落不上」，而实际灌库后 591 行是有归属的 ——
    报告与事实相反，比没有报告更糟。

    **每行都要进某一个桶。** 早先只统计「有集群类型/赛道列」的行，
    于是《浙江省产业带企业调研清单》那种只有「产业带」列的清单会得到
    「0 组 / 0 行」——不是「全部落位」，也不是「全部落空」，而是**什么都没算**，
    而页面上的「集群可落位 0 / 0 行」看起来却像个正常结论。
    所以这里始终返回 total，并且把「按哪一列落的」单独统计（``bases``）。
    """
    from collections import Counter

    from ..seed.companies import newstar_slug_index

    newstar = newstar_slug_index()
    by_key: dict[tuple, dict] = {}
    bases: Counter = Counter()
    total = resolved_total = 0

    for row in rows:
        total += 1
        slug = row.get("list_cluster_slug") or ""
        basis = "" if slug else ""
        if slug:
            basis = row.get("list_cluster_basis") or "清单自带"
        else:
            slug = resolve_cluster_slug(row, newstar)
            if slug:
                basis = "按赛道 / 整份归属"
        if slug:
            resolved_total += 1
            bases[basis] += 1

        # 分组标签：优先用清单自己声明的归属列（产业带 / 块状经济 / 产区）原文，
        # 其次才是赛道 / 集群类型 —— 前者是这一行**为什么**属于那个集群的依据。
        label = " ".join(
            part
            for part in (
                row.get("list_cluster_name"),
                row.get("cluster_type"),
                row.get("track"),
                row.get("sub_track"),
                row.get("cluster_district"),
            )
            if part
        ) or "（清单未给归属列）"
        key = (
            row.get("list_cluster_name") or "",
            row.get("cluster_type") or "",
            row.get("track") or "",
            row.get("sub_track") or "",
            row.get("cluster_district") or "",
        )
        entry = by_key.setdefault(
            key,
            {
                "label": label,
                "cluster_type": key[1],
                "cluster_district": key[4],
                "track": key[2],
                "sub_track": key[3],
                "rows": 0,
                "resolved_rows": 0,
                "unresolved_rows": 0,
                "slugs": Counter(),
            },
        )
        entry["rows"] += 1
        if slug:
            entry["resolved_rows"] += 1
            entry["slugs"][slug] += 1
        else:
            entry["unresolved_rows"] += 1

    groups = sorted(by_key.values(), key=lambda e: (-e["rows"], e["label"]))
    for entry in groups:
        entry["slugs"] = dict(entry["slugs"].most_common(8))
        # 「落位到哪」要能直接读出来：一个赛道可能分散到多个县的新星产业群
        entry["slug_summary"] = "、".join(f"{slug}（{n} 行）" for slug, n in entry["slugs"].items())
    unresolved = [entry for entry in groups if entry["unresolved_rows"]]
    return {
        "total_rows": total,
        "resolved_rows": resolved_total,
        "unresolved_rows": total - resolved_total,
        "bases": dict(bases.most_common()),
        "requests": groups,
        "unresolved": unresolved,
        "note": "unresolved 里的行不会挂到任何集群（企业仍会入库，只是没有集群归属）",
    }


def _database_delta(session: Session, rows: list[dict], dry_run: bool, source_key: str = "") -> dict:
    """相对**当前库里 / 其余名单里**的关系：导入是覆盖式的，所以「还剩多少家」也要清楚。

    ``source_key`` 用来把**本次要覆盖的那份名单**从对照里摘出去：导入前它的旧行
    已经在库里、也在骨架里，不摘的话「已经在别的名单里」会把这一整份名单自己
    算进去（实测 317 行全部自报命中），那个数字就等于没信息。
    """
    known = {name for (name,) in session.execute(select(Enterprise.name)).all()}
    incoming = {row["name"] for row in rows}
    others = {c["name"] for c in load_company_skeletons(exclude_source_key=source_key)}
    return {
        "existing_in_db": len(known),
        "incoming": len(incoming),
        # 覆盖式导入：这份名单导入前已有的行，导入后**不会**消失（会被新行取代），
        # 所以「会少掉多少家」= 库里有、但这批不再收录、且不属于其它名单的家数。
        "dropped_from_db": sorted(known - incoming - others),
        "new_to_db": len(incoming - known),
        "also_in_other_lists": len([n for n in incoming if n in others]),
        "dry_run": dry_run,
    }
