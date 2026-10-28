"""Canonical company list, merged from the source lists.

**这个模块是后端 ``app/seed/companies.py`` 的 CLI 镜像：两边的合并规则必须一模一样**
（``tests/test_tools.py`` 的「两份骨架实现除集群归属外完全相同」守着这一条），
所以本文件里的合并逻辑是**从后端拷过来**的 —— 改一边就要同步改另一边。
两边唯一允许的差异是 `cluster_slugs`（归属映射只在入库时算）。
"""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE_DIR = ROOT / "data" / "source"
CURATED_DIR = ROOT / "data" / "curated"

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
# 来源文件 → 该名单整体的核实程度。
#
# 两个 PDF 是**官方名单**（`official`）；武义户外运动与休闲清单是**第三方调研清单**
# （`site_curated`），必须分开标注：把一份调研清单和工信部公示名单显示成同一个
# 核实程度，会直接毁掉「官方认定」这个标签的含义。
SOURCE_FILES = (
    ("xiaojuren.json", "official"),
    ("zhejiang_mingpin_2026.json", "official"),
    (OUTDOOR_FILE, "site_curated"),
    (SEAMLESS_FILE, "site_curated"),
    (CLUSTER_REPS_FILE, "site_curated"),
)


def imported_lists() -> list[dict]:
    """``/api/admin/lists/import`` 导入的名单（注册表）。

    这个文件是 CLI 与后端**共同的事实来源**，所以两边都要读它；
    这里照抄一份而不是 import backend，是为了让
    ``tests/test_tools.py`` 里「两份骨架实现必须一致」的断言仍然有意义 ——
    若这里改成 import，那条断言就成了同义反复。
    """
    path = CURATED_DIR / "imported_lists.json"
    if not path.exists():
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return []
    return [entry for entry in payload.get("lists", []) if entry.get("source_key")]


def source_files() -> tuple[tuple[str, str], ...]:
    """内置名单 + 导入的名单，按文件名去重（同名即同一份，后导入的覆盖核实程度）。"""
    merged: dict[str, str] = dict(SOURCE_FILES)
    for entry in imported_lists():
        if entry.get("source_json") and entry.get("grade"):
            merged[entry["source_json"]] = entry["grade"]
    return tuple(merged.items())





def load_companies(exclude_source_key: str = "") -> list[dict]:
    """One record per company, with list names / 415X tracks / products merged.

    ``exclude_source_key`` 与 ``seed.companies.load_company_skeletons`` 同一语义
    （两份骨架必须给出同一条记录，测试会比对它们）。导入报告用它算
    「这批企业与**其余**名单的关系」。
    """
    merged: dict[str, dict] = {}

    skip_file = ""
    if exclude_source_key:
        entry = next((e for e in imported_lists() if e.get("source_key") == exclude_source_key), {})
        skip_file = entry.get("source_json", "")

    for filename, grade in source_files():
        if skip_file and filename == skip_file:
            continue
        path = SOURCE_DIR / filename
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
            # 地区按**来源档位**择一，不按文件读取顺序 —— 顺序是 ``SOURCE_FILES``
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
            record["sources"].append({"name": row["list_name"], "file": f"data/source/{filename}"})

    return sorted(merged.values(), key=lambda r: r["name"])
