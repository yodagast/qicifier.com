"""Canonical company skeleton, merged from every source list.

Each source list is parsed into ``data/source/*.json``; this module is the single
place that decides *which* companies exist. Never read ``enriched.json`` /
``discovered.json`` / ``manual_sites.json`` to decide that — those only carry
contact details. ``tools/companies.py`` mirrors this for CLI use, and
``tests/test_tools.py`` asserts the two produce exactly the same set.

``SOURCE_FILES`` maps each list to its **核实程度** as well as its file: the two
PDFs are 官方名单 (``official``), while 武义县户外运动与休闲企业清单 is a
third-party research list (``site_curated``). A research list must never inherit
the official label just because it flows through the same code path.

名单不必写死在这里：``/api/admin/lists/import`` 导入的名单会登记到
``data/curated/imported_lists.json``，``source_files()`` 把注册表并进内置表。
新增一份清单因此不需要改代码。
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

from ..config import settings
from ..services.list_import import imported_lists
from .regions import CITIES

# 名单级核实程度的优先级：官方认定 > 政府公开 > 媒体/协会 > 站方整理 > 企业自报。
# 与 schemas.GRADE_LABELS 的五档一一对应。
GRADE_RANK = {
    "official": 5,
    "gov_open": 4,
    "media": 3,
    "site_curated": 2,
    "self_reported": 1,
}

# 地区列的来源档位。与 GRADE_RANK 分开：这里衡量的不是「名单有多权威」，而是
# **这一行的地区值指的是谁**。
#
# 镇街级/产业带调研清单逐行写的是**产区**的县市区（宁波均胜挂在瑞安塘下产区名下），
# 那是一个产区坐标，不是这家企业的注册地。
# 所以判据是：省外行（地区整段不可用于挂浙江政区）< 调研清单（产区坐标）
# < 官方名单/政府底表（逐行写的是企业自己的所在地）。`benchmark`（拿来对标的龙头）
# 的列值同样不是它自己的，但那种行的地区由 ``geocode_row`` 从地址重新认，
# 不参与这里的「谁的值更可信」比较。
REGION_VALUE_RANK = {
    "out_of_province": 0,
    "research_list": 3,
    "company": 4,
}


def region_value_rank(row: dict, grade: str) -> int:
    """这一行的地区值能证明什么 —— 合并多份名单时用它决定采用哪一行的值。"""
    if row.get("out_of_province"):
        return REGION_VALUE_RANK["out_of_province"]
    if grade in ("official", "gov_open"):
        return REGION_VALUE_RANK["company"]
    return REGION_VALUE_RANK["research_list"]

OUTDOOR_FILE = "wuyi_outdoor_2026.json"
SEAMLESS_FILE = "seamless_garment_2026.json"
CLUSTER_REPS_FILE = "cluster_reps.json"
# 内置名单：随代码一起维护的那几份（两份官方 PDF + 两份调研清单 + 第四批代表企业）
BUILTIN_SOURCE_FILES = (
    ("xiaojuren.json", "official"),
    ("zhejiang_mingpin_2026.json", "official"),
    (OUTDOOR_FILE, "site_curated"),
    (SEAMLESS_FILE, "site_curated"),
    (CLUSTER_REPS_FILE, "site_curated"),
)


def source_files() -> tuple[tuple[str, str], ...]:
    """内置名单 + 通过 ``/api/admin/lists/import`` 导入的名单。

    按**文件名**去重：接口导入的名单指的就是同一份 ``data/source/<key>.json``，
    再导入一次是**更新**它，不是又添一份 —— 所以注册表里的条目覆盖内置条目
    （核实程度也随之以注册表为准）。
    """
    merged: dict[str, str] = dict(BUILTIN_SOURCE_FILES)
    for entry in imported_lists():
        filename = entry.get("source_json")
        grade = entry.get("grade")
        if filename and grade:
            merged[filename] = grade
    return tuple(merged.items())


# 第三方调研清单 → 它对应的产区集群。
#
# 清单本身就是归属依据，所以 attribution 记成 research_list，而不是靠主营产品
# 猜出来的 product；也不能落进 district_fallback，那会把它标成「归属待确认」，
# 反而把有依据的归属说成没依据。
# 这些产业群本身就是官方第四批「新星」产业群（newstar-18 / newstar-19），
# 所以直接挂官方集群 —— 不再另建同名集群。
LIST_CLUSTER = {
    "武义县户外运动与休闲企业清单（第三方调研）": "newstar-19-户外运动与休闲",
    "全国无缝服装企业清单（第三方调研）": None,  # 按「产区」逐行决定，见 PRODUCTION_AREA_CLUSTER
}

# 全国性清单：一家企业属于哪个集群由它所在的**产区**决定，不能整份清单挂一个集群
PRODUCTION_AREA_CLUSTER = {
    "浙江义乌": "newstar-18-无缝服装",
    "广东汕头": "shantou-gurao-seamless",
    "广东佛山": "foshan-yanbu-brands",
    "福建晋江": "jinjiang-shenhu-underwear",
    # 修订版（137 行）把「产区」细化到「地方 + 品类」，并多出两个产区。
    # 两种粒度的键都留着：旧的 19 行清单仍在库里（两份名单合并存档），
    # 而它用的就是上面那套粗粒度产区名。
    #
    # 为什么这里要**显式**写一遍、不靠导入器算好的 list_cluster_slug：
    # 「合并」这个决定意味着新旧两份清单同时在线，旧那份（以及它派生的一切）
    # 没有 slug 兜底，只能靠这一列。值必须与导入器的 PRODUCTION_AREA_INDEX 一致 ——
    # 两份文件各写一遍是有意的：``seed.companies`` 与 ``tools/companies.py``
    # 必须给出同一条骨架，谁也不能 import 对方的表。
    "义乌无缝针织": "newstar-18-无缝服装",
    "浦江针织服装": "pujiang-zhenzhi-fuzhuang",
    "汕头谷饶针织内衣": "shantou-gurao-seamless",
    "汕头潮南内衣": "shantou-chaonan-underwear",
    "佛山盐步内衣": "foshan-yanbu-brands",
    "晋江深沪内衣": "jinjiang-shenhu-underwear",
}





def newstar_slug_index() -> dict[tuple[str, str], str]:
    """(县市区, 细分赛道) → 官方「新星」产业群 slug。"""
    path = settings.source_dir / "zhejiang_clusters_batch4.json"
    if not path.exists():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    return {
        (row["district"], row["cluster"]): f"newstar-{row['seq']:02d}-{row['cluster']}"
        for row in payload.get("新星产业群", [])
    }


def strip_city_prefix(value: str) -> str:
    """「杭州市余杭区」→「余杭区」。官方名单里的县市区字段不带设区市前缀。"""
    for _, city in CITIES:
        if value.startswith(city):
            return value[len(city):]
    return value


def cluster_reps_slug(row: dict, newstar: dict[tuple[str, str], str]) -> str:
    """「第四批产业集群代表企业」的一行 → 它代表的**已有官方集群** slug。

    核心区/协同区挂在对应的 415X 赛道集群上（那个集群的 cluster_region 里
    已经记了哪些县是这个赛道的核心区/协同区）；「新星」产业群挂到对应的
    newstar-NN 集群。这一份清单不新建集群。
    """
    kind = row.get("cluster_type", "")
    if kind in ("核心区", "协同区"):
        return f"415X-{row.get('track', '')}"
    if kind == "新星产业群":
        district = strip_city_prefix(row.get("cluster_district", ""))
        return newstar.get((district, row.get("sub_track", "")), "")
    return ""


# 「新星」产业群与 415X 赛道的**县市区写法**并不统一，而赛道列只给到「赛道」。
# 两处必须归一，否则「天台县」永远对不上「天台经济开发区」：
#   * 撤市设区：奉化市 → 奉化区
#   * 开发区挂在县名下：天台经济开发区 ⊂ 天台县
# 只做「去掉末尾行政区后缀再比」+「带开发区字样的嵌套」，不做模糊拼音匹配 ——
# 归属错了比不归属更难发现。
_DISTRICT_SPLIT_SUFFIXES = ("经济开发区", "开发区", "新区")


def _district_core(value: str) -> str:
    core = (value or "").strip()
    for suffix in _DISTRICT_SPLIT_SUFFIXES:
        if core.endswith(suffix):
            return core[: -len(suffix)]
    # 「天台县」「奉化市」「柯桥区」都是三字以上，去掉末尾一字不会把「义乌」削成「义」
    if len(core) > 2 and core[-1] in "市区县":
        return core[:-1]
    return core


def same_district(left: str, right: str) -> bool:
    """两个县市区写法是否指同一地（见上面三种归一）。"""
    a, b = _district_core(left), _district_core(right)
    if not a or not b:
        return False
    return a == b or (len(a) >= 2 and len(b) >= 2 and (a.startswith(b) or b.startswith(a)))


@lru_cache(maxsize=1)
def known_415x_slugs() -> frozenset[str]:
    """415X 赛道集群的 slug（``415X-<赛道名>``）。

    读 ``curated/clusters_seed.json``；读不到就返回空集 —— 那时不猜归属，
    与「没有这份文件就没有这些集群」的事实一致。
    """
    path = settings.curated_dir / "clusters_seed.json"
    if not path.exists():
        return frozenset()
    payload = json.loads(path.read_text(encoding="utf-8"))
    return frozenset(
        row["slug"] for row in payload.get("tracks", []) if row.get("slug")
    )


def track_cluster_slug(row: dict, newstar: dict[tuple[str, str], str]) -> str:
    """行的「赛道 / 产业赛道」列 → 集群 slug（认不出来返回空串）。

    只认两种**有据可依**的落位：

    1. 赛道名本身就是某个 415X 赛道 → ``415X-<赛道>``；
    2. 赛道名对应某个官方「新星」产业群，**且该企业所在县市区就是那个产业群的县**。
       「新星」是按县命名的（兰溪市中医药大健康、柯桥区光电…），
       企业不在那个县却挂过去，就是把 A 县的产业群算到 B 县头上。

    做不到以上两条就不落位。此前导出一份带「赛道」列的多赛道清单时，
    这里只有「整份清单挂一个集群」一条路，14/21 个赛道全部落空，
    报告却仍显示「成功」—— 见 ``routers/admin_lists.py`` 的回归用例。
    """
    track = (row.get("track") or "").strip()
    if not track:
        return ""
    slug = f"415X-{track}"
    if slug in known_415x_slugs():
        return slug
    district = row.get("district") or row.get("cluster_district") or ""
    # 清单的县市区列偶尔带设区市前缀（「杭州市余杭区」），官方名单里不带。
    district = strip_city_prefix(district)
    hits = {
        newstar_slug
        for (newstar_district, newstar_name), newstar_slug in newstar.items()
        if same_district(newstar_district, district)
        and (newstar_name in track or track in newstar_name)
    }
    # 同一县可能有多个候选（「文体用品与合成材料」同时对应开化文体用品与龙港合成材料），
    # 那就说明这一列的信息不足以定位，不落位。
    return hits.pop() if len(hits) == 1 else ""


def resolve_cluster_slug(row: dict, newstar: dict[tuple[str, str], str]) -> str:
    """一行记录 → 它对应的集群 slug（认不出来返回空串）。

    五种依据，按可信度排序：

    1. ``list_cluster_slug``：**解析阶段**就算好的（镇街级清单按「块状经济」列分派），
       它比重新猜一遍准得多，所以排第一；
    2. 整份清单声明自己属于某个集群（``LIST_CLUSTER``）；
    3. 按企业所在产区（``PRODUCTION_AREA_CLUSTER``，全国性清单用）；
    4. 按行内的集群列（「全国代表企业」清单用）；
    5. 按行内的「赛道」列（见 ``track_cluster_slug``）。放在最后：行内集群列
       说的是「它代表哪个集群」，比赛道这一列更具体。
    """
    slug = row.get("list_cluster_slug") or None
    if slug is None:
        slug = LIST_CLUSTER.get(row["list_name"])
    if slug is None:
        slug = PRODUCTION_AREA_CLUSTER.get(row.get("production_area", ""))
    if slug is None and row.get("cluster_type"):
        slug = cluster_reps_slug(row, newstar)
    # 行内已声明集群类型时不再按赛道兜底：那两份依据会互相打架，
    # 而「集群类型」是清单里更明确的那一个。
    if slug is None and not row.get("cluster_type"):
        slug = track_cluster_slug(row, newstar)
    return slug or ""


def load_company_skeletons(exclude_source_key: str = "") -> list[dict]:
    """One record per company, with list names / 415X tracks / products merged.

    ``exclude_source_key`` 把某一份名单从合并里摘掉。两个用途：
    导入报告要算「这批企业与**其余**名单的关系」（不摘的话这份名单会和自己对上一次，
    317 行全部自报「已在别的名单里」）；以及修名单时想看清「去掉它还剩什么」。
    """
    merged: dict[str, dict] = {}
    newstar = newstar_slug_index()

    skip_file = ""
    if exclude_source_key:
        entry = next((e for e in imported_lists() if e.get("source_key") == exclude_source_key), {})
        skip_file = entry.get("source_json", "")

    for filename, grade in source_files():
        if skip_file and filename == skip_file:
            continue
        path = settings.source_dir / filename
        if not path.exists():
            continue
        for row in json.loads(path.read_text(encoding="utf-8")):
            name = row["name"].strip()
            record = merged.setdefault(
                name,
                {
                    "name": name,
                    "list_names": [],
                    "products": [],
                    "tracks": [],
                    "city": "",
                    "district": "",
                    "town": "",
                    "region_raw": "",
                    # 这一行的地区值能证明什么（见 region_value_rank）。合并时取最高的一档。
                    "region_rank": -1,
                    # 解析阶段按「块状经济」列算好的归属（镇街级清单）。留空表示
                    # 这份清单没有自带归属，那就交给 resolve_cluster_slug 去推断。
                    "list_cluster_slug": "",
                    # 地区是怎么来的：官方名单原文 / 第三方调研清单 / 省外（本库无对应行政区）
                    "region_source": "",
                    "sources": [],
                    # 名单级核实程度：一条记录可能同时出现在官方名单与调研清单里，
                    # 取**最高**的那一档（官方认定优先）。
                    "data_grade": grade,
                    "verify_tier": "",
                    "founded_year": None,
                    "registered_capital": "",
                    "note": "",
                    # 一家企业可能同时是多个集群的代表（清单里出现多次），所以是列表；
                    # 第一项作为主集群。
                    "cluster_slugs": [],
                    "website_kind": "",
                    # 展示名去掉股票代码/品牌名括注后的登记名 —— 验收门用它去页面里找。
                    # 拿「科大讯飞股份有限公司（002230）」去找，任何页面都匹配不上。
                    "match_name": "",
                    "business_model": "",
                    "address_registered": "",
                },
            )
            # 名单级核实程度取**最高**的一档。以前只有「官方 / 调研」两种，
            # 写成 `if grade == "official"` 就够了；现在有三档（+ 政府公开），
            # 再那么写就变成「取决于名单读取顺序」——同一个企业会因为
            # 底表排在调研清单前面还是后面而显示不同的档位。
            if GRADE_RANK.get(grade, 0) > GRADE_RANK.get(record["data_grade"], 0):
                record["data_grade"] = grade
            if row["list_name"] not in record["list_names"]:
                record["list_names"].append(row["list_name"])
            # 主营产品**原文整条保留，不按「/」拆**：官方名单里的「/」是型号分隔符
            # （`（6305X2/P63）`、`（25g/m2）`），拆了就毁数据；调研名单里的「/」才是
            # 并列产品，但同一份数据里两种含义混在一起，拆不准就不拆。
            product = (row.get("product") or "").strip()
            if product and product not in record["products"]:
                record["products"].append(product)
            if row.get("track") and row["track"] not in record["tracks"]:
                record["tracks"].append(row["track"])
            # 地区来源与「有没有 city」解耦：省外企业本来就没有浙江行政区可挂，
            # 但它仍然带着「来自调研清单 / 省外」这个事实，不能因此退回默认的 official_list。
            # 「对标龙头」同理：清单说它不是**这个产区**的企业，可它在浙江 ——
            # 一律记成省外，就会把浙江企业算进省外数（天堂伞在萧山、均胜在宁波、
            # 森马与人本在温州，这一条以前就是这么错的）。
            # 官方名单先处理，所以一旦有官方来源，后面再多的调研清单也改不掉它。
            if grade == "official":
                record["region_source"] = record["region_source"] or "official_list"
            elif row.get("out_of_province"):
                record["region_source"] = "out_of_province"
            elif row.get("benchmark"):
                # `or` 而不是直接覆盖：两家企业后来被确认为浙江的产区企业时，
                # 那一份清单会把它标成 research_list，别让早先那条「对标」标记
                # 把它一直钉在 benchmark 上（「对标」是**这一份清单**的产区关系判断）。
                record["region_source"] = record["region_source"] or "benchmark"
            else:
                record["region_source"] = record["region_source"] or "research_list"
            # 地区按**来源档位**择一，不按文件读取顺序 —— 顺序是 `source_files()`
            # 的实现细节，让它决定企业页显示哪个地区，等于把「谁先被读到」当判据。
            # 实测后果：产业带清单排在后面时，均胜显示区空、中银电池显示「宁波高新区」。
            rank = region_value_rank(row, grade)
            if row.get("city") and rank > record["region_rank"]:
                record["city"] = row["city"]
                record["district"] = row.get("district", "")
                record["town"] = row.get("town", "")
                record["region_raw"] = row.get("region_raw", "")
                record["region_rank"] = rank
                if grade == "official":
                    record["region_source"] = "official_list"
            # 调研名单独有的字段
            if row.get("verify_tier") and not record["verify_tier"]:
                record["verify_tier"] = row["verify_tier"]
            if row.get("founded_year") and not record["founded_year"]:
                record["founded_year"] = row["founded_year"]
            if row.get("registered_capital") and not record["registered_capital"]:
                record["registered_capital"] = row["registered_capital"]
            if row.get("note") and not record["note"]:
                record["note"] = row["note"]
            if row.get("list_cluster_slug") and not record["list_cluster_slug"]:
                record["list_cluster_slug"] = row["list_cluster_slug"]
            if row.get("match_name") and not record["match_name"]:
                record["match_name"] = row["match_name"]
            if row.get("website_kind") and not record["website_kind"]:
                record["website_kind"] = row["website_kind"]
            if row.get("business_model") and not record["business_model"]:
                record["business_model"] = row["business_model"]
            if row.get("address_registered") and not record["address_registered"]:
                record["address_registered"] = row["address_registered"]
            cluster_slug = resolve_cluster_slug(row, newstar)
            if cluster_slug and cluster_slug not in record["cluster_slugs"]:
                record["cluster_slugs"].append(cluster_slug)
            record["sources"].append({"name": row["list_name"], "file": f"data/source/{filename}"})

    return sorted(merged.values(), key=lambda r: r["name"])
