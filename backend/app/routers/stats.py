"""``/api/stats`` — 首页统计与数据来源披露."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import Integer, cast, func, select
from sqlalchemy.orm import Session

from ..database import get_session
from ..models import Cluster, Enterprise, Region
from ..seed.companies import load_company_skeletons
from ..schemas import GRADE_LABELS, StatsOut
from ..services.list_import import imported_lists

router = APIRouter(prefix="/api/stats", tags=["stats"])


@router.get("", response_model=StatsOut)
def get_stats(session: Session = Depends(get_session)):
    enterprise_total = session.scalar(select(func.count(Enterprise.id))) or 0
    # 为了对比收录的省外产区（广东汕头/佛山、福建晋江）单独计数：
    # 本库是浙江产业带库，把省外样本混进「浙江企业数」等于换个方式说谎。
    enterprise_out_of_province = (
        session.scalar(
            select(func.count(Enterprise.id)).where(
                Enterprise.region_source == "out_of_province"
            )
        )
        or 0
    )
    enterprise_official = (
        session.scalar(select(func.count(Enterprise.id)).where(Enterprise.data_grade == "official")) or 0
    )
    with_website = (
        session.scalar(
            select(func.count(Enterprise.id)).where(Enterprise.website != "")
        )
        or 0
    )
    with_phone = (
        session.scalar(
            select(func.count(Enterprise.id)).where(
                Enterprise.public_phone != ""
            )
        )
        or 0
    )
    cluster_total = session.scalar(select(func.count(Cluster.id))) or 0
    cluster_official = (
        session.scalar(select(func.count(Cluster.id)).where(Cluster.data_grade == "official")) or 0
    )
    region_total = session.scalar(select(func.count(Region.id))) or 0
    track_total = (
        session.scalar(select(func.count(Cluster.id)).where(Cluster.slug.like("415X-%"))) or 0
    )

    list_rows = session.execute(
        select(
            func.unnest(Enterprise.list_names).label("list_name"),
            func.count(Enterprise.id),
        ).group_by("list_name")
    ).all()
    # 名单级核实程度从骨架层取，不能写死 —— 这里以前对所有名单都填 "official"，
    # 第三方调研清单接进来之后就成了把调研清单标成官方认定。
    list_grade = {}
    for company in load_company_skeletons():
        for name in company["list_names"]:
            list_grade.setdefault(name, company["data_grade"])

    grade_rows = session.execute(
        select(Enterprise.data_grade, func.count(Enterprise.id))
        .group_by(Enterprise.data_grade)
    ).all()

    return StatsOut(
        enterprise_total=enterprise_total,
        enterprise_out_of_province=enterprise_out_of_province,
        enterprise_official=enterprise_official,
        enterprise_with_website=with_website,
        enterprise_with_phone=with_phone,
        cluster_total=cluster_total,
        cluster_official=cluster_official,
        region_total=region_total,
        track_total=track_total,
        list_breakdown=[
            {
                "name": name,
                "count": count,
                "grade": list_grade.get(name, "official"),
                # 标签也一并给出：前端原来对整张表写死「官方认定」，
                # 表里混进第三方调研清单之后那句话就成了假的。
                "grade_label": GRADE_LABELS.get(list_grade.get(name, "official"), ""),
            }
            for name, count in list_rows
        ],
        grade_breakdown=[
            {"grade": grade, "label": GRADE_LABELS.get(grade, grade), "count": count}
            for grade, count in grade_rows
        ],
    )


@router.get("/sources")
def get_sources(session: Session = Depends(get_session)):
    """数据来源与核实程度声明（对应调研方案 10 节来源地图）."""
    return {
        "grades": [
            {"key": "official", "label": GRADE_LABELS["official"],
             "meaning": "来自官方名单、政府公示、上市/新三板公告等具有认定效力的材料；页面附来源与核验日期"},
            {"key": "gov_open", "label": GRADE_LABELS["gov_open"],
             "meaning": "来自政府发布的描述性材料（统计公报、政府工作报告、新闻发布会实录等），权威但非认定"},
            {"key": "media", "label": GRADE_LABELS["media"],
             "meaning": "署名媒体报道或行业协会公开材料，注明授予主体与年份"},
            {"key": "site_curated", "label": GRADE_LABELS["site_curated"],
             "meaning": "站方整理，未逐条核实，仅作为检索线索使用"},
            {"key": "self_reported", "label": GRADE_LABELS["self_reported"],
             "meaning": "企业通过入驻表单自行填写，本站未核验"},
        ],
        "sources": [
            {"name": "第七批专精特新“小巨人”企业公示名单", "role": "企业骨架",
             "publisher": "工业和信息化部中小企业局",
             "grade": "official", "file": "docs/1第七批专精特新“小巨人”企业公示名单pdf.pdf",
             "used_for": "浙江省 306 家企业，作为企业骨架名单"},
            {"name": "《2026年浙江制造精品推广目录》公示名单", "role": "企业骨架 · 赛道与产品",
             "publisher": "浙江省经济和信息化厅",
             "grade": "official", "file": "docs/产业群名单.pdf",
             "used_for": "浙江省 323 家企业及其 415X 集群赛道、产品名称、所属地区；同时是 15 个 “415X” 集群赛道名称的原文来源"},
            {"name": "《第四批省级重点产业集群核心区协同区和“新星”产业群公示名单》", "role": "集群地理归属",
             "publisher": "浙江省经济和信息化厅（2026-09-23 公示）",
             "grade": "official",
             "file": "docs/第四批省级重点产业集群核心区协同区和“新星”产业群公示名单.pdf",
             "used_for": "8 个核心区、7 个协同区的官方地理归属（也就是「哪些县属于哪个集群」这张对照关系）；22 个“新星”产业群（B 级档案）"},
            {"name": "《浙江省“415X”先进制造业集群建设行动方案》（2023-01 印发）", "role": "产业分类",
             "publisher": "浙江省人民政府", "grade": "official", "used_for": "415X 顶层产业分类"},
            {"name": "浙江省民政厅区划地名处 / 国家统计局统计用区划代码", "role": "地理层",
             "grade": "official", "used_for": "11 市 / 90 县（市、区）地理层；省级新区/开发区另用 Z 前缀自定义码，不计入 90 个县级行政区"},
            {"name": "企业官网公开页面（首页 / 联系我们 / 版权行）", "role": "联系方式", "grade": "official",
             "used_for": "官网地址、官网公示的总机/400 电话与公开邮箱；每一条都附官网来源页与核验日期"},
            {"name": "人工补充（站方核对）", "role": "联系方式", "grade": "site_curated",
             "used_for": "站方核验不出官网的企业，由维护者手工填写公开联系方式；页面逐字段标「人工补充 · 站方核对」"},
            {"name": "搜索结果摘要（聚合站 / 目录站）", "role": "联系方式", "grade": "site_curated",
             "used_for": "官网无法核实的企业，从搜索结果摘要文本中读取其公开总机与地址；"
                         "未访问过原站，页面逐字段标「搜索结果摘要 · 原站未核验」，"
                         "跨企业重复出现的号码（聚合站客服电话）一律丢弃，"
                         "非浙江区号且非 400/800 的号码不予采信；"
                         "该层为历史跑批留下的少量记录，已停止扩展——覆盖率现由各调研清单提供"},
            {"name": "武义县户外运动与休闲企业清单（第三方调研）", "role": "产区企业层", "grade": "site_curated",
             "file": "data/source/wuyi_outdoor_2026.csv",
             "used_for": "金华市武义县户外运动与休闲产业 65 家企业，作为该产区的企业层；"
                         "名单本身不是官方认定，逐行保留调研方核实档位（A 官网公示 7 / B 工商可核 10 / C 线索未核 48），"
                         "名单里给出的官网只作候选，须通过本站验收门才会以「站方核验 · 已比对官网」入库"},
            {"name": "全国无缝服装企业清单（第三方调研）", "role": "产区企业层", "grade": "site_curated",
             "file": "docs/全国无缝服装企业清单.csv",
             "used_for": "义乌无缝针织服装产区 11 家 + 省外对比产区 8 家（汕头谷饶、佛山盐步、晋江深沪）；"
                         "逐行保留调研方核实档位（A 官网公示 4 / B 电话待补或工商可核 6 / C 线索 9）；"
                         "规模指标带口径与年份，两组产能口径（设备能力 / 产品产量）不混用"},
            {"name": "各设区市 / 县（市、区）政府门户", "role": "规模数字入口", "grade": "gov_open",
             "used_for": "规模数字与产业描述的来源入口（B 级档，尚待逐条摘录）"},
        ] + _imported_sources(),
        "boundaries": [
            "只展示公开地址与官网公示的企业联系方式，不含任何个人联系方式（手机号一律不采集、不展示）",
            "各名单口径、层级、发布主体不同，互不相加、互不比较；"
            "官方名单与第三方调研清单必须分别计数，不得合并成一个「收录企业总数」当作官方口径",
            "第三方调研清单只代表调研方的核实程度，本站不对其结论背书；"
            "这些企业的官网仍要独立通过本站验收门，联系方式逐字段标注来源与核实档位",
            "第三方调研清单的 C 档（纯第三方线索、未核验）照常展示，但页面逐字段标注"
            "「第三方调研 · 未核线索」——可信度靠标签交代，不靠隐藏；"
            "调研清单的「可用范围」原本建议 C 档不对外展示，本站未采纳该建议",
            "省外产区（广东汕头、广东佛山、福建晋江）仅作对比参照，不参与浙江产区的任何统计",
            "「对标龙头」是拿来与产区对比的行业龙头，不是该产区的企业：它们单独标记、"
            "不参与该产区的统计，其地址也不能代表产区位置（施耐德在上海、立讯精密在东莞）；"
            "其中 4 家（天堂伞、均胜、森马、人本）企业本身就在浙江，省外与否按企业自己的地址判，"
            "不把它们划出浙江统计",
            "调研清单里的「地市 / 县市区 / 镇街」列写的是「产区」的位置，不是企业注册地"
            "（宁波均胜挂在瑞安塘下产区名下）；这类行的企业地区只按它自己的地址与名称前缀认，"
            "认不出就留空 —— 宁可显示不出地区，也不把产区坐标当成企业的所在地",
            "地图按县级政区汇总：来源名单没给地址、企业名里也不含地名的浙江企业落不到县一级，"
            "地图上点不到，但它们仍在企业名录里可检索 —— 地图装得下的家数不等于收录总数",
            "镇街级产区的核实档位只说明「官网是否公示」，不代表号码当前有效 —— "
            "调研方未做电话回拨核验，页面上的号码性质（官网总机 / 第三方总机 / 待核）逐条标注",
            "「有官网」与「打得通电话」在这类产区里是两件事："
            "不少企业有官网但不公示座机（浦江水晶 6 家里 5 家有官网、0 家公示座机），"
            "所以官网覆盖率不可当作可联系率使用",
            "排序只依据公开可核验程度与资料完整度，对所有企业一致；不收入驻费、不卖排名、不接受竞价",
            "不下单、不收款、不展示报价、不撮合、不抽成、不保证回复",
            "站方整理内容（镇街级块状经济、官网候选清单）单独标记，不与官方认定同款式呈现",
        ],
        "account": [
            "注册不需要邮箱或短信验证；本站不会保存明文密码",
            "登录状态用签名令牌维持（默认 30 天），用户可随时退出清除",
            "收藏是私有数据：只有本人可见，不进入企业页面、列表、公开接口，也不参与排序",
            "账号信息不用于营销、不与第三方共享；收藏仅用于用户个人的采购备忘",
        ],
    }


def _imported_sources() -> list[dict]:
    """通过 ``/api/admin/lists/import`` 导入的名单 → 来源声明条目。

    数字取自注册表（导入时写下的），说明文字取自导入时填的 ``used_for``：
    名单一修订，声明里的数字跟着变，不会留一句「共 161 条」的旧话在网上。
    """
    out = []
    for entry in imported_lists():
        tiers = " / ".join(f"{tier} {count}" for tier, count in sorted(entry.get("tiers", {}).items()))
        used_for = entry.get("used_for") or (
            f"{entry.get('companies', '?')} 家企业，逐行保留清单方核实档位（{tiers}）；"
            f"清单给出的官网只作候选，须通过本站验收门才会以「站方核验 · 已比对官网」入库"
        )
        out.append({
            "name": entry["list_name"],
            # 角色在导入时由维护者指定；老条目没有就留空，前端不显示这一行。
            "role": entry.get("role", ""),
            "grade": entry["grade"],
            "file": entry.get("source_file", ""),
            "used_for": used_for,
            "imported_at": entry.get("imported_at", ""),
        })
    return out