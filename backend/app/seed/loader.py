"""Seed the database from the parsed sources + curated reference data.

Everything loaded here is traceable to a file under ``data/``:

* ``data/source/xiaojuren.json``            — 第七批专精特新“小巨人”企业公示名单（浙江 306 家）
* ``data/source/zhejiang_mingpin_2026.json``—《2026年浙江制造精品推广目录》公示名单（浙江 323 家）
* ``data/curated/clusters_seed.json``       — 415X 赛道 + 镇街级块状经济
* ``data/curated/enriched.json``            — 企业官网/电话/邮箱（站方整理 + 逐条抓取校验）

Idempotent: re-running replaces cluster/enterprise rows but keeps ids stable by
matching on the natural key (cluster slug, enterprise name).
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import settings
from ..models import Cluster, ClusterRegion, Enterprise, EnterpriseCluster, Region
from ..services.cluster_edits import apply_cluster_edits
from ..services.contacts import load_contacts
from .regions import DISTRICT_ALIASES, GOV_PORTALS, all_regions

CITY_TO_CODE = {name: code for code, name in ((r["code"], r["name"]) for r in all_regions() if r["level"] == "city")}




def load_json(path: Path):
    if not path.exists():
        return []
    return json.loads(path.read_text(encoding="utf-8"))


def seed_regions(session: Session) -> dict[str, Region]:
    existing = {r.code: r for r in session.scalars(select(Region)).all()}
    for row in all_regions():
        region = existing.get(row["code"])
        if region is None:
            region = Region(code=row["code"], name=row["name"], level=row["level"],
                            parent_code=row["parent_code"], sources=[])
            session.add(region)
            existing[row["code"]] = region
        else:
            region.name = row["name"]
            region.level = row["level"]
            region.parent_code = row["parent_code"]
    session.flush()
    return existing


# 集群来源文件（依次合并）。
#
# ``clusters_seed.json`` 由 tools/build_seed.py **整体重写**，所以调研清单派生的集群
# 必须另放一个文件，否则下次重建骨架时会被无声删掉。
CLUSTER_FILES = ("clusters_seed.json", "research_clusters.json")


def seed_clusters(session: Session, regions: dict[str, Region]) -> dict[str, Cluster]:
    clusters = {c.slug: c for c in session.scalars(select(Cluster)).all()}

    town_by_name = {r.name: r for r in regions.values() if r.level == "town"}
    district_by_name = {r.name: r for r in regions.values() if r.level == "district"}
    city_by_name = {r.name: r for r in regions.values() if r.level == "city"}

    payloads = [load_json(settings.curated_dir / name) for name in CLUSTER_FILES]
    payload = payloads[0]

    for row in payload.get("tracks", []):
        cluster = clusters.get(row["slug"])
        if cluster is None:
            cluster = Cluster(slug=row["slug"])
            session.add(cluster)
            clusters[row["slug"]] = cluster
        cluster.name = row["name"]
        cluster.industry_l1 = row.get("industry_l1", row["name"])
        cluster.industry_l2 = row.get("industry_l2", "")
        cluster.sector = row.get("sector", "")
        cluster.tier = row.get("tier", "A")
        cluster.scale_tier = row.get("scale_tier", "")
        cluster.data_grade = row.get("data_grade", "official")
        cluster.summary = row.get("tier_note", "")
        # 这段话说的是「这些赛道名是从哪来的」，是站方对自己数据来源的说明，
        # 不是名单原文 —— 标成官方认定会让「官方认定」这个标签贬值。
        cluster.summary_grade = "site_curated"
        cluster.sources = [
            {
                "name": "《2026年浙江制造精品推广目录》公示名单",
                "grade": "official",
                "url": "docs/产业群名单.pdf",
            }
        ]

    for source in payloads:
        for row in source.get("clusters", []):
            cluster = clusters.get(row["slug"])
            if cluster is None:
                cluster = Cluster(slug=row["slug"])
                session.add(cluster)
                clusters[row["slug"]] = cluster
            cluster.name = row["name"]
            cluster.aliases = row.get("aliases", [])
            cluster.industry_l1 = row.get("industry_l1", "")
            cluster.industry_l2 = row.get("industry_l2", "")
            cluster.sector = row.get("sector", "")
            cluster.tier = row.get("tier", "C")
            cluster.scale_tier = row.get("scale_tier", "块状经济")
            cluster.core_area = row.get("core_area", "")
            cluster.honors = row.get("honors", [])
            # 规模数字一律走 metric_set（值 + 口径 + 年份 + 来源），禁止裸数字。
            # 调研清单派生的集群只有在这里带上口径，前端那张表才敢显示。
            cluster.metrics = row.get("metric_set", [])
            cluster.data_grade = row.get("data_grade", "site_curated")
            cluster.summary = row.get("note", "")
            cluster.summary_grade = row.get("summary_grade", "site_curated")
            cluster.sources = [
                {
                    "name": row.get("source_name")
                    or "站方整理（镇街级块状经济，无统一官方名单，未逐条核验）",
                    "grade": row.get("data_grade", "site_curated"),
                }
            ]
            if row.get("city") in GOV_PORTALS:
                cluster.sources.append(
                    {
                        "name": f"{row['city']}政府门户（统计公报/政府工作报告为 B 级档来源入口）",
                        "grade": "gov_open",
                        "url": GOV_PORTALS[row["city"]],
                    }
                )

    session.flush()

    session.query(ClusterRegion).delete()
    session.flush()
    for source in payloads:
        for row in source.get("clusters", []):
            cluster = clusters[row["slug"]]
            if row.get("out_of_province"):
                # 省外对比产区**故意**不挂行政区：本库的地理层是浙江的，
                # 随手挂到某个同名区县上会造出一条假的地理关系。
                continue
            town = town_by_name.get(row.get("town", ""))
            district = district_by_name.get(row.get("district", ""))
            # 市级归口的产区（医疗器械那 9 条有 7 条是这种：成员散在好几个县区，
            # 产区名写的是市）。不挂任何行政区的话，集群页会显示「未标注产地」——
            # 可我们明明知道它在哪个市。挂到市一级，角色写清是「市级归口」。
            city = city_by_name.get(row.get("city", "")) if not district and not town else None
            for region, role, primary in (
                (town, "唯一产地", True),
                (district, "核心区", False),
                (city, "市级归口", False),
            ):
                if region is None:
                    continue
                session.add(
                    ClusterRegion(cluster_id=cluster.id, region_id=region.id, role=role, is_primary=primary)
                )
    session.flush()
    return clusters


def link_tracks_to_districts(session: Session, regions: dict[str, Region]) -> int:
    """仅当缺少官方核心区/协同区名单时，才按「企业地区 × 415X 赛道」派生地理关系。

    有官方名单时由 seed_official_cluster_roles() 写入 核心区/协同区，
    派生的「协同区」会被清掉，避免把推断结果当成官方认定。
    """
    by_id = {r.id: r for r in regions.values()}
    pairs = session.execute(
        select(Enterprise.region_id, EnterpriseCluster.cluster_id)
        .join(EnterpriseCluster, EnterpriseCluster.enterprise_id == Enterprise.id)
        .join(Cluster, Cluster.id == EnterpriseCluster.cluster_id)
        .where(Cluster.slug.like("415X-%"), Enterprise.region_id.is_not(None))
        .distinct()
    ).all()

    existing = {
        (link.cluster_id, link.region_id)
        for link in session.scalars(select(ClusterRegion).where(ClusterRegion.role == "协同区")).all()
    }
    added = 0
    for region_id, cluster_id in pairs:
        if region_id not in by_id or (cluster_id, region_id) in existing:
            continue
        session.add(
            ClusterRegion(cluster_id=cluster_id, region_id=region_id, role="协同区", is_primary=False)
        )
        existing.add((cluster_id, region_id))
        added += 1
    session.flush()
    return added


# 企业名称前缀 → 县级行政区（用于「所属地区」缺失时的地区推断，页面标为站方整理）
# 仅收录无歧义前缀；源名单所在地与推断地冲突时不推断，直接留空。
NAME_PREFIX_DISTRICT = {
    "杭州": ("杭州市", "滨江区"),
    "宁波": ("宁波市", "鄞州区"),
    "温州": ("温州市", "鹿城区"),
    "嘉兴": ("嘉兴市", "南湖区"),
    "湖州": ("湖州市", "吴兴区"),
    "绍兴": ("绍兴市", "越城区"),
    "金华": ("金华市", "婺城区"),
    "衢州": ("衢州市", "柯城区"),
    "舟山": ("舟山市", "定海区"),
    "台州": ("台州市", "椒江区"),
    "丽水": ("丽水市", "莲都区"),
    # 县级市 / 县 名作为企业名前缀（源名单中较常见）
    "乐清": ("温州市", "乐清市"),
    "瑞安": ("温州市", "瑞安市"),
    "永嘉": ("温州市", "永嘉县"),
    "平阳": ("温州市", "平阳县"),
    "苍南": ("温州市", "苍南县"),
    "龙港": ("温州市", "龙港市"),
    "诸暨": ("绍兴市", "诸暨市"),
    "嵊州": ("绍兴市", "嵊州市"),
    "新昌": ("绍兴市", "新昌县"),
    "海宁": ("嘉兴市", "海宁市"),
    "平湖": ("嘉兴市", "平湖市"),
    "桐乡": ("嘉兴市", "桐乡市"),
    "嘉善": ("嘉兴市", "嘉善县"),
    "海盐": ("嘉兴市", "海盐县"),
    "德清": ("湖州市", "德清县"),
    "长兴": ("湖州市", "长兴县"),
    "安吉": ("湖州市", "安吉县"),
    "余姚": ("宁波市", "余姚市"),
    "慈溪": ("宁波市", "慈溪市"),
    "宁海": ("宁波市", "宁海县"),
    "象山": ("宁波市", "象山县"),
    "兰溪": ("金华市", "兰溪市"),
    "义乌": ("金华市", "义乌市"),
    "东阳": ("金华市", "东阳市"),
    "永康": ("金华市", "永康市"),
    "武义": ("金华市", "武义县"),
    "浦江": ("金华市", "浦江县"),
    "磐安": ("金华市", "磐安县"),
    "江山": ("衢州市", "江山市"),
    "龙游": ("衢州市", "龙游县"),
    "常山": ("衢州市", "常山县"),
    "开化": ("衢州市", "开化县"),
    "温岭": ("台州市", "温岭市"),
    "临海": ("台州市", "临海市"),
    "玉环": ("台州市", "玉环市"),
    "天台": ("台州市", "天台县"),
    "仙居": ("台州市", "仙居县"),
    "三门": ("台州市", "三门县"),
    "龙泉": ("丽水市", "龙泉市"),
    "青田": ("丽水市", "青田县"),
    "缙云": ("丽水市", "缙云县"),
    "遂昌": ("丽水市", "遂昌县"),
    "松阳": ("丽水市", "松阳县"),
    "云和": ("丽水市", "云和县"),
    "庆元": ("丽水市", "庆元县"),
    "景宁": ("丽水市", "景宁畲族自治县"),
    "岱山": ("舟山市", "岱山县"),
    "嵊泗": ("舟山市", "嵊泗县"),
}


def infer_region_from_name(name: str) -> tuple[str, str] | None:
    """据企业名称前缀推断 (市, 县)。仅用于源名单缺「所属地区」时，页面需标注为推断。"""
    for prefix, location in sorted(NAME_PREFIX_DISTRICT.items(), key=lambda kv: -len(kv[0])):
        if name.startswith(prefix):
            return location
    return None


def match_region(regions: dict[str, Region], city: str, district: str) -> Region | None:
    lookup = dict(DISTRICT_ALIASES).get((city, district), district)
    for region in regions.values():
        if region.level == "district" and region.name == lookup:
            parent = regions.get(region.parent_code or "")
            if parent and parent.name == city:
                return region
    return None


def build_enterprises() -> list[dict]:
    """官方名单里的企业骨架（谁被收录只看名单，不看联系方式文件）。"""
    from .companies import load_company_skeletons

    return load_company_skeletons()


def parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def summarize(record: dict) -> str:
    parts = []
    if record["tracks"]:
        parts.append("415X 赛道：" + "、".join(record["tracks"]))
    if record["products"]:
        parts.append("收录产品：" + "；".join(record["products"][:2]))
    parts.append("来源名单：" + "；".join(record["list_names"]))
    return "。".join(parts)


def cluster_town_index(
    session: Session, regions: dict[str, Region], clusters: dict[str, Cluster]
) -> dict[str, Cluster]:
    """镇/街道名 → 该镇街的主集群（镇街级块状经济），用于归属兜底。"""
    by_id = {c.id: c for c in clusters.values()}
    town_names = {r.id: r.name for r in regions.values() if r.level == "town"}
    index: dict[str, Cluster] = {}
    for link in session.scalars(select(ClusterRegion).where(ClusterRegion.is_primary.is_(True))).all():
        name = town_names.get(link.region_id)
        cluster = by_id.get(link.cluster_id)
        if name and cluster and not cluster.slug.startswith("415X-"):
            index[name] = cluster
    return index


# 官方名单的发布主体。**只有列在这里的名单才是官方认定** ——
# 名单名对不上的一律按「第三方调研」处理，宁可标低不可标高。
LIST_GRANTORS = {
    "第七批专精特新“小巨人”企业公示名单": "工业和信息化部中小企业局",
    "《2026年浙江制造精品推广目录》公示名单": "浙江省经济和信息化厅",
    "《第四批省级重点产业集群核心区协同区和“新星”产业群公示名单》": "浙江省经济和信息化厅",
}


def build_honors(record: dict) -> list[dict]:
    """企业「为什么被收录」，逐条带核实程度。

    以前这里把所有名单都盖成「官方认定」并写死授予单位，第三方调研清单接进来之后
    就变成了把调研清单说成官方认定 —— 这种错误对外是硬伤，所以改成按名单查表。
    """
    honors = []
    for name in record["list_names"]:
        grantor = LIST_GRANTORS.get(name)
        if grantor:
            honors.append({"name": name, "granted_by": grantor, "grade": "official", "verify_tier": ""})
        else:
            honors.append(
                {
                    "name": name,
                    "granted_by": "第三方调研机构（非官方认定）",
                    "grade": record.get("data_grade", "site_curated"),
                    "verify_tier": record.get("verify_tier", ""),
                    "note": "第三方调研清单，逐行标注核实档位；不代表任何官方认定",
                }
            )
    return honors


def seed_enterprises(session: Session, regions: dict[str, Region], clusters: dict[str, Cluster]) -> int:
    track_lookup = {c.name: c for c in clusters.values() if c.slug.startswith("415X-")}
    town_by_district: dict[str, list[str]] = {}
    for region in regions.values():
        if region.level == "town" and region.parent_code:
            town_by_district.setdefault(region.parent_code, []).append(region.name)
    cluster_by_town = cluster_town_index(session, regions, clusters)
    contacts = load_contacts()

    by_name = {e.name: e for e in session.scalars(select(Enterprise)).all()}

    session.query(EnterpriseCluster).delete()
    session.flush()

    count = 0
    for record in build_enterprises():
        enterprise = by_name.get(record["name"])
        if enterprise is None:
            enterprise = Enterprise(name=record["name"])
            session.add(enterprise)
            by_name[record["name"]] = enterprise
        region = match_region(regions, record["city"], record["district"])
        region_source = record.get("region_source") or "official_list"
        # 省外企业不挂浙江行政区；「对标龙头」同理 —— 它的所在地只能来自自己的地址，
        # 不能拿企业名里的地名去凑一个县（「宁波均胜」不代表它就在宁波某个区）。
        if region is None and region_source not in ("out_of_province", "benchmark"):
            inferred = infer_region_from_name(record["name"])
            if inferred:
                # 名单未给「所属地区」时按企业名称前缀推断，页面必须标注为推断
                region = match_region(regions, inferred[0], inferred[1])
                if region is not None:
                    record["city"], record["district"] = inferred
                    region_source = "name_prefix"
        enterprise.city = record["city"]
        enterprise.district = record["district"]
        enterprise.region_id = region.id if region else None
        enterprise.region_source = region_source

        enterprise.products = record["products"]
        enterprise.list_names = record["list_names"]
        enterprise.honors = build_honors(record)
        enterprise.summary = summarize({**record, "products": record["products"]})
        enterprise.founded_year = record.get("founded_year")
        enterprise.registered_capital = record.get("registered_capital", "")
        enterprise.verify_tier = record.get("verify_tier", "")
        enterprise.business_model = record.get("business_model", "")
        enterprise.address_registered = record.get("address_registered", "")

        # 联系方式：手工补充 > 自动发现校验 > 站方候选抓取；逐字段带来源标签
        contact = contacts.get(record["name"], {})
        enterprise.website = contact.get("website", "")
        enterprise.website_kind = contact.get("website_kind", "")
        enterprise.phone_nature = contact.get("phone_nature", "")
        enterprise.address_type = contact.get("address_type", "")
        enterprise.public_phone = contact.get("public_phone", "")
        enterprise.public_email = contact.get("public_email", "")
        enterprise.address = contact.get("address", "")
        enterprise.contact_sources = contact.get("sources", {})
        enterprise.contact_status = contact.get("contact_status", "empty")
        # 人工补充的企业属性优先于名单里带的原值（人工层就是最高优先级）
        if contact.get("founded_year"):
            try:
                enterprise.founded_year = int(contact["founded_year"])
            except (TypeError, ValueError):
                pass
        if contact.get("registered_capital"):
            enterprise.registered_capital = str(contact["registered_capital"])[:64]
        enterprise.source_url = contact.get("source_url", "")
        enterprise.verified_at = parse_dt(contact.get("verified_at"))
        enterprise.enrich_status = contact.get("enrich_status", "pending")
        enterprise.manual_note = contact.get("manual_note", "")

        # 核实程度来自**名单**（官方名单 official / 第三方调研 site_curated），
        # 不能再无条件写 "official"：武义调研清单走的也是这条代码路径。
        enterprise.data_grade = record.get("data_grade", "official")
        session.flush()

        # 同一家企业可能被多条途径挂到同一个集群上：
        # 官方名单按「415X 赛道」算出来一次，调研清单又把它列为该集群的代表企业。
        # 那两条会撞 `uq_enterprise_cluster`，所以用 `linked` 记账去重 ——
        # 保留先写进去的那条（`product` 与 `research_list` 指向同一个集群，
        # 换标签只会丢掉原有的依据）。
        attributed = False
        linked: set[int] = set()
        for index, track in enumerate(record["tracks"]):
            cluster = track_lookup.get(track)
            if cluster is None or cluster.id in linked:
                continue
            attributed = True
            linked.add(cluster.id)
            session.add(
                EnterpriseCluster(
                    enterprise_id=enterprise.id,
                    cluster_id=cluster.id,
                    is_primary=index == 0,
                    attribution="product",
                    needs_confirmation=False,
                )
            )

        # 产区调研清单：清单自带归属依据，优先于下面的「按镇街兜底」。
        # 放在兜底之前是必须的——否则有依据的归属会被降级成「归属待确认」。
        for slug in record.get("cluster_slugs") or []:
            list_cluster = clusters.get(slug)
            if list_cluster is None:
                print(f"  warn: 调研清单指向的集群不存在：{slug}（{record['name']}）")
                continue
            if list_cluster.id in linked:
                attributed = True
                continue
            session.add(
                EnterpriseCluster(
                    enterprise_id=enterprise.id,
                    cluster_id=list_cluster.id,
                    # 一家企业可能是多个集群的代表；已有归属时不再抢主集群
                    is_primary=not linked,
                    attribution="research_list",
                    needs_confirmation=False,
                )
            )
            linked.add(list_cluster.id)
            attributed = True

        # 兜底（调研方案 §04）：没有 415X 赛道可依据时，归入注册地所在镇街的主导集群，
        # 并在页面标「归属待确认」，同时保留更正入口。
        if not attributed and region is not None:
            for town_name in town_by_district.get(region.code, []):
                fallback = cluster_by_town.get(town_name)
                if fallback is None:
                    continue
                enterprise.town = town_name
                session.add(
                    EnterpriseCluster(
                        enterprise_id=enterprise.id,
                        cluster_id=fallback.id,
                        is_primary=True,
                        attribution="district_fallback",
                        needs_confirmation=True,
                    )
                )
                break
        count += 1

    session.flush()
    mark_industry_regions(session, regions)
    return count


def load_batch4() -> dict:
    """第四批省级重点产业集群核心区/协同区/"新星"产业群（官方认定）。"""
    path = settings.source_dir / "zhejiang_clusters_batch4.json"
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def seed_newstar_clusters(session: Session, regions: dict[str, Region], clusters: dict[str, Cluster]) -> int:
    """把 22 个「新星」产业群建成 B 级集群档案（官方认定，带细分赛道）。"""
    payload = load_batch4().get("新星产业群", [])
    if not payload:
        return 0
    district_by_name = {r.name: r for r in regions.values() if r.level == "district"}
    town_by_name = {r.name: r for r in regions.values() if r.level == "town"}
    added = 0

    for row in payload:
        slug = f"newstar-{row['seq']:02d}-{row['cluster']}"
        cluster = next((c for c in clusters.values() if c.slug == slug), None)
        if cluster is None:
            cluster = Cluster(slug=slug)
            session.add(cluster)
            clusters[slug] = cluster
        cluster.name = f"{row['district']}{row['cluster']}"
        cluster.industry_l1 = row["highland"]
        cluster.industry_l2 = row["cluster"]
        cluster.sector = row["highland"]
        cluster.tier = "B"
        cluster.scale_tier = "新星产业群"
        cluster.core_area = row["district"]
        cluster.data_grade = "official"
        cluster.summary = (
            f"第四批省级重点产业集群“新星”产业群（{row['highland']} · {row['cluster']}），"
            f"认定主体：{row['city']}{row['district']}。"
        )
        # 这句话里的每一项（档次、产业高地、细分赛道、认定主体）都逐字来自官方公示名单，
        # 所以标官方认定；换成站方自己归纳的段落时同样要改这个字段。
        cluster.summary_grade = "official"
        cluster.honors = [
            {
                "name": "第四批省级重点产业集群“新星”产业群",
                "granted_by": row["publisher"],
                "year": int(row["published_at"][:4]),
                "grade": "official",
            }
        ]
        cluster.sources = [
            {"name": row["list_name"], "grade": "official", "url": row["source_url"]}
        ]
        cluster.data_grade = "official"
        session.flush()

        district = district_by_name.get(row["district"]) or town_by_name.get(row["district"])
        if district is None:
            print(f"  warn: 找不到行政区 {row['city']}{row['district']}（新星产业群 #{row['seq']}）")
            continue
        exists = session.scalar(
            select(ClusterRegion).where(
                ClusterRegion.cluster_id == cluster.id, ClusterRegion.region_id == district.id
            )
        )
        if exists is None:
            session.add(
                ClusterRegion(
                    cluster_id=cluster.id, region_id=district.id, role="核心区", is_primary=True
                )
            )
        added += 1

    session.flush()
    return added


def seed_official_cluster_roles(
    session: Session, regions: dict[str, Region], clusters: dict[str, Cluster]
) -> int:
    """用官方的核心区/协同区认定覆盖自动派生的地理关系（方案 §01 主骨架）。

    先把「派生协同区」清掉，再按官方名单写入 role，避免把推断结果当成官方认定。
    """
    payload = load_batch4()
    if not payload.get("核心区"):
        return 0

    official_clusters = [c for c in clusters.values() if c.slug.startswith("415X-")]
    by_name = {c.name: c for c in official_clusters}
    by_id = {c.id: c for c in official_clusters}
    derived = session.scalars(
        select(ClusterRegion).where(ClusterRegion.role == "协同区")
    ).all()
    for link in derived:
        if link.cluster_id in by_id:
            session.delete(link)
    session.flush()

    district_by_name = {r.name: r for r in regions.values() if r.level == "district"}
    town_by_name = {r.name: r for r in regions.values() if r.level == "town"}

    def resolve(name: str) -> Region | None:
        return district_by_name.get(name) or town_by_name.get(name)

    written = 0
    for role in ("核心区", "协同区"):
        for row in payload.get(role, []):
            cluster = by_name.get(row["cluster"])
            if cluster is None:
                print(f"  warn: 集群名未匹配到 415X 赛道：{row['cluster']}")
                continue
            district = resolve(row["district"])
            if district is None:
                print(f"  warn: 找不到行政区 {row['city']}{row['district']}")
                continue
            exists = session.scalar(
                select(ClusterRegion).where(
                    ClusterRegion.cluster_id == cluster.id, ClusterRegion.region_id == district.id
                )
            )
            if exists is None:
                session.add(
                    ClusterRegion(
                        cluster_id=cluster.id,
                        region_id=district.id,
                        role=role,
                        is_primary=role == "核心区",
                    )
                )
                written += 1
            else:
                exists.role = role
                exists.is_primary = role == "核心区"
    session.flush()
    return written


def mark_industry_regions(session: Session, regions: dict[str, Region]) -> None:
    """Flag regions that actually carry enterprise/cluster records (``has_industry``)."""
    for region in regions.values():
        region.has_industry = False
    used: set[int] = set()
    for enterprise in session.scalars(select(Enterprise)).all():
        if enterprise.region_id:
            used.add(enterprise.region_id)
    for link in session.scalars(select(ClusterRegion)).all():
        used.add(link.region_id)
    for region_id in used:
        region = session.get(Region, region_id)
        if region is None:
            continue
        region.has_industry = True
        parent = regions.get(region.parent_code or "")
        if region.level == "town" and parent:
            parent.has_industry = True
    for name, code in CITY_TO_CODE.items():
        city = regions.get(code)
        if city and any(r.has_industry for r in regions.values() if r.parent_code == code):
            city.has_industry = True
    session.flush()


def run(session: Session) -> dict:
    regions = seed_regions(session)
    clusters = seed_clusters(session, regions)

    # 官方核心区/协同区/「新星」产业群必须在**企业入库之前**建成。
    #
    # 调研清单把企业直接挂到这些官方集群上（`enterprise_cluster.cluster_id`），
    # 顺序反了就会挂空 —— 第一版正是因此给义乌无缝、武义户外各另建了一个集群，
    # 同一个官方产业群出现两条档案。
    seed_official_cluster_roles(session, regions, clusters)
    if not load_batch4().get("核心区"):
        link_tracks_to_districts(session, regions)  # 无官方名单时的兜底
    newstar = seed_newstar_clusters(session, regions, clusters)
    if newstar:
        print(f"  新星产业群：{newstar} 个已建档（官方认定）")
    apply_research_overlays(session, clusters)
    # 站方在管理界面维护的产业带档案优先级最高（官方名单 → 调研叠加 → 本站编辑）
    edited = apply_cluster_edits(session, clusters)
    if edited:
        print(f"  站方维护的产业带档案：{edited} 个已叠加")

    total = seed_enterprises(session, regions, clusters)
    session.commit()
    return {"regions": len(regions), "clusters": len(clusters), "enterprises": total}


def apply_research_overlays(session: Session, clusters: dict[str, Cluster]) -> int:
    """把调研清单的成果叠加到**官方集群**上（规模指标、荣誉、别名、备注）。

    集群主体仍以官方名单为准（名字、档次、核实程度），调研只补齐它没有的东西。
    这样就不会出现「同一个产业群两条档案」。
    """
    payload = load_json(settings.curated_dir / "research_clusters.json")
    applied = 0
    for row in payload.get("overlays", []):
        cluster = clusters.get(row["slug"])
        if cluster is None:
            print(f"  warn: 叠加目标集群不存在：{row['slug']}")
            continue
        if row.get("aliases"):
            cluster.aliases = sorted(set(cluster.aliases or []) | set(row["aliases"]))
        if row.get("metric_set"):
            cluster.metrics = row["metric_set"]
        if row.get("honors"):
            cluster.honors = row["honors"]
        if row.get("note"):
            cluster.summary = row["note"]
            # 简介这一段**文字是站方根据调研材料整理的**，所以标「站方整理」，
            # 而不是照抄叠加层的 source_grade（那说的是它下面那些规模数字的来源）。
            # 标成「媒体 · 协会来源」会让读者以为整段话引自某篇报道 —— 它没有署名主体。
            cluster.summary_grade = "site_curated"
        if row.get("source_name"):
            cluster.sources = [
                {"name": row["source_name"], "grade": row.get("source_grade", "site_curated")},
                *[s for s in (cluster.sources or []) if s.get("name") != row["source_name"]],
            ]
        applied += 1
    session.flush()
    return applied


if __name__ == "__main__":
    from ..database import SessionLocal

    with SessionLocal() as session:
        print(run(session))