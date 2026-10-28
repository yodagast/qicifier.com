"""通用「名单 CSV」导入：一份外部分档清单 → 三份标准产物 + 注册表条目。

为什么要有这个模块，而不是继续给每份清单写一个 ``tools/parse_*.py``：

* 每份清单的**解析规则其实是一样的**（哨兵值、手机号、官网类型、登记名、地址认地区），
  只有列名和档位文案不同。给每份清单写一遍，就等于把同一批坑重踩一遍。
* 清单是**会被修订的**（``..._副本.csv`` 就是同一份名单的修订版）。列表长了、条目变了，
  都不该再动代码。

因此这里把规则集中一处，产物仍是既有的那三类文件，下游（骨架合并 / 联系方式分层 /
官网候选）不需要知道数据是从脚本来的还是从接口来的：

    data/source/<source_key>.json           证据层：逐行原样 + raw 子对象
    data/curated/<source_key>_list.json     联系方式层：逐行带 source_label
    data/curated/<source_key>_sites.json    候选官网层：只作候选，必须过验收门

**同一 source_key 再导入 = 覆盖该名单的整份数据**，这正是「名单修订」的语义：
把 add/remove 全部生效，而不是往里追加。新增一份名单则用一个新 source_key。

导入完成后必须重新灌库（``python -m app.cli seed --drop``）才会出现在页面上 ——
接口不会替调用方动数据库，那些产物文件才是数据源。
"""

from __future__ import annotations

import csv
import io
import json
import os
import re
import tempfile
from datetime import datetime, timezone, timedelta
from pathlib import Path

from ..config import settings
from ..seed.regions import CITIES, DISTRICT_ALIASES, DISTRICTS, TOWNS

REGISTRY_FILE = "imported_lists.json"

# ---------------------------------------------------------------------------
# 列名映射：清单的作者换一列名不该导致导入失败，但**列的含义**必须唯一
# ---------------------------------------------------------------------------
COLUMN_ALIASES: dict[str, tuple[str, ...]] = {
    "name": ("公司名称", "企业名称", "企业名", "公司名", "name"),
    "website": ("官网", "网站", "网址", "官方网站", "website"),
    "website_kind": ("官网类型", "网站类型"),
    "phone": ("电话", "联系电话", "公开电话", "phone"),
    "email": ("邮箱", "电子邮箱", "email"),
    "address": ("地址", "企业地址", "注册地址", "address"),
    "note": ("备注", "说明", "note"),
    "verify_tier": ("核实等级", "核实档位", "档位", "核实程度"),
    "cluster_type": ("集群类型",),
    "cluster_district": ("集群（县市区）", "集群(县市区)", "集群属地", "集群所在地"),
    "track": ("所属产业赛道", "产业赛道", "赛道"),
    "sub_track": ("细分赛道",),
    "product": ("主营产品", "主要产品", "产品"),
    "production_area": ("产区", "生产产区"),
    "founded_year": ("成立年份", "成立时间"),
    "registered_capital": ("注册资本",),
    "address_registered": ("注册地址（如与办公地不同）", "注册地"),
    "business_model": ("经营模式",),
    "batch": ("批次",),
    "seq": ("序号", "编号"),
    # 第五份（镇街级块状经济）带来的列。这些**不是**通用列：
    # 「块状经济 / 镇街 / 地市 / 县市区」共同决定一家企业挂到哪个集群，
    # 「电话性质 / 地址类型 / 是否本地产区」则决定这条线索能不能当数据用。
    "city": ("地市", "设区市", "市"),
    "district": ("县市区", "区县"),
    "town": ("镇街", "镇（街道）", "乡镇", "所属街道"),
    "cluster_name": ("块状经济", "块状经济名称", "产业带"),
    "phone_nature": ("电话性质",),
    "address_type": ("地址类型",),
    "local_scope": ("是否本地产区", "产地范围"),
    "industry_tag": ("产业",),
    # 第六份（临安区规上企业基本信息，政府公开数据）的列。
    # 这些列名是市场监管/统计口径的固定写法，直接用它们做别名。
    "credit_code": ("企业统一信用代码", "统一社会信用代码", "信用代码"),
    "industry_name": ("所属行业名称", "行业名称"),
    "industry_code": ("行业代码",),
    "industry_group": ("产业名称", "区级产业分组"),
    "industry_group_code": ("产业代码",),
    "established": ("成立日期", "成立时间"),
    "founded_year": ("成立年份", "成立时间", "成立日期"),
    "operating_status": ("经营状态",),
}

# 官网类型 → 这个链接能不能当「官网」。
#
# 调研文档专门警告过：把第三方商铺填进「官网」字段，采购商点进去看到的是中间平台
# 而不是工厂本身。所以只有企业自有站点（或其所属集团/母公司站点）才进 ``website``。
SELF_SITE_KINDS = {
    "企业官网",
    "科研机构官网",
    "品牌站",
    "品牌海外站",
}
# 同一个集团多家子公司共用一个域名（三家安费诺全指向 amphenol.com）。
# 这类链接**收**，但 ``website_kind`` 必须如实带出去，页面上要说明它指向集团。
PARENT_SITE_KINDS = {
    "集团官网",
    "母站企业官网",
    "母公司官网",
}
NOT_SELF_KINDS = {
    "第三方商铺",
    "第三方／集团站",
    "第三方/集团站",
}
# 「没有自己的站」：不是一个链接，而是「这件事本身」——原样写进备注备查
NO_SITE_KINDS = {
    "无独立官网",
    "待补",
    "待核",
}

# 已识别的官网类型全集。「未知类型」不能默认当成企业自有站点 ——
# 那等于替清单做判断：哪天清单多一个「经销商站」，就会被当成工厂官网发出去。
KNOWN_KINDS = SELF_SITE_KINDS | PARENT_SITE_KINDS | NOT_SELF_KINDS | NO_SITE_KINDS

WEBSITE_SENTINELS = ("待补", "无独立官网", "无官网", "待核", "未获取", "未公示", "占位符")
# 电话/地址列里表示「没有」的写法。这里用**整串**匹配，不用子串 ——
# 「无」做子串会命中「无锡市」，「-」做子串会命中「qj-robot.com」。
PHONE_SENTINELS = ("待补", "未获取", "未公示", "占位符", "—", "-", "无")
ADDRESS_SENTINELS = ("待补", "未获取", "未公示", "未知", "无", "—", "-")

MOBILE_SHAPE = re.compile(r"^(?:\+?86)?1[3-9]\d{9}$")
EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$")
DOMAIN_SHAPE = re.compile(r"[a-z0-9-]+(\.[a-z0-9-]+)+", re.I)
# 地址里要有具体落点才算展示地址；只有「北京市」这类也行（对全国代表企业来说
# 「在哪个城市」已经是有用信息），但纯哨兵值不行
ADDRESS_SHAPE = re.compile(r"[省市区县路街镇乡号村园大厦栋幢号楼]")

# 展示名里的括注（股票代码、品牌名）不是登记名的一部分，验收门要拿登记名去页面里找 ——
# 拿「科大讯飞股份有限公司（002230）」去找，任何页面都匹配不上。
NAME_ANNOTATION = re.compile(r"^(.+?(?:公司|集团|厂|研究院|实验室|中心|大学|研究所))([（(].*)?$")

# 档位文案 → 逐行标签的「细节」部分。清单自己怎么分档，页面就怎么标；
# 认不出来的档位不猜含义，只报 warning，标签退到「未复核」。
TIER_DETAILS = (
    ("官网公示", "官网公示（未复核）"),
    ("工商", "工商可核（联系方式来自第三方）"),
    ("第三方", "未核线索"),
    ("线索", "未核线索"),
)
# 只有等级字母、没有中文分档说明的清单（第五份清单就是）。
# 字母的含义沿用该体系既定的口径，所以可以直接映射，不必要求清单再写一遍。
TIER_LETTERS = {"A": "官网公示（未复核）", "B": "工商可核（联系方式来自第三方）", "C": "未核线索"}
GRADE_PREFIX = {
    "official": "官方名单",
    "gov_open": "政府公开",
    "media": "媒体 · 协会",
    "site_curated": "第三方调研",
    "self_reported": "企业自报",
}


def _now() -> str:
    return datetime.now(timezone(timedelta(hours=8))).isoformat(timespec="seconds")


def resolve_columns(header: list[str]) -> dict[str, str]:
    """规范列名 → CSV 里的实际列名（取第一个命中的别名）。"""
    found: dict[str, str] = {}
    for key, aliases in COLUMN_ALIASES.items():
        for alias in aliases:
            if alias in header and key not in found:
                found[key] = alias
    return found


def match_name_of(name: str) -> str:
    """登记名（去掉结尾的股票代码/品牌名括注）；认不出来就原样返回。

    只剥**结尾**的同层括注：``中航（成都）无人机系统股份有限公司`` 里的「（成都）」
    是法定名称的一部分，剥掉就匹配不上了。
    """
    found = NAME_ANNOTATION.match(name.strip())
    return found.group(1) if found else name.strip()


def split_sentinel_note(raw: str) -> tuple[str, str]:
    """把 ``待补（官网公示号码被打码）`` 拆成 ``("待补", "官网公示号码被打码")``。

    「待补」只是状态，**括号里那句才是为什么**。丢掉它，读者看到电话栏空白
    只会以为我们漏了；留下它，才知道是来源自己没给。
    """
    value = (raw or "").strip()
    if not value:
        return "", ""
    found = re.search(r"[（(]([^）)]*)[）)]", value)
    if not found:
        return value, ""
    outside = (value[: found.start()] + value[found.end():]).strip(" 　/／、,，;；")
    return outside, found.group(1).strip()


def clean_website(raw: str) -> str:
    """CSV 里常存裸域名（``tongdun.com``），补上协议；哨兵值一律返回空串。"""
    value = (raw or "").strip()
    # 破折号/斜杠这类**符号**整串匹配（做子串会把带连字符的域名全灭）
    if not value or value in ("—", "-", "/") or any(s in value for s in WEBSITE_SENTINELS):
        return ""
    if not value.startswith(("http://", "https://")):
        if not re.fullmatch(r"[a-z0-9.-]+\.[a-z]{2,}", value, re.I):
            return ""
        value = "https://" + value
    return value.rstrip("/")


def sentinel_lead(raw: str) -> str:
    """哨兵值里夹带的线索（``待补（杭钢集团 hzsteel.com）``）。

    只**原样引用**，不做任何提取：那不是这家企业自己的站点
    （否则作者就不会写「待补」），揪出来填进官网字段就是替清单做判断了。
    """
    value = (raw or "").strip()
    if not value:
        return ""
    found = re.search(r"[（(]([^）)]+)[）)]", value)
    if not found:
        return ""
    lead = found.group(1).strip()
    # 只在意「话里带了个域名」的那种线索；「母公司创世纪 300083」这类不是链接，
    # 原值已经在 raw 里留档，不必再复述一遍。
    return lead if DOMAIN_SHAPE.search(lead) else ""


def normalize_landline(raw: str) -> str:
    """一个号码 → 展示形态（``0571-88943332`` / ``400-068-9796``），不合法返回空串。

    只做**形态**校验（区号 + 7/8 位、400/800 十位），不用全国区号白名单：
    白名单是给「从网页正文里捞数字」准备的（防止把时间戳当电话），
    而这里是清单里一个专门写电话的列，过严只会把真实号码判死。
    """
    value = (raw or "").strip()
    # 国家码前缀（+86 / 0086 / 86-）不是号码的一部分：剥掉再判形态。
    # 这是国际业务线常见的写法，不剥会整条判死，而读者只会看到「号码形态不合法」。
    #
    # 注意国际写法还会**省掉长途冠码 0**（``+86-512-…`` 其实是 ``0512-…``），
    # 所以剥完要把 0 补回来 —— 但手机号（``+86-138…``，11 位、以 1 开头）不能补，
    # 补了就变成一个不存在的 ``0138…``。
    prefixed = re.sub(r"^\s*(?:\+?86|0086)[-\s]?", "", value) != value
    value = re.sub(r"^\s*(?:\+?86|0086)[-\s]?", "", value)
    if prefixed:
        parts = re.match(r"^(\d{2,3})[- ]", value)
        if parts and not parts.group(1).startswith(("0", "1")):
            value = "0" + value
    digits = re.sub(r"\D", "", value)
    if not digits:
        return ""
    if digits.startswith(("400", "800")):
        return f"{digits[:3]}-{digits[3:6]}-{digits[6:10]}" if len(digits) == 10 else ""
    if MOBILE_SHAPE.fullmatch(digits):
        return ""
    # 3 位区号只有 010 / 02x，其余都是 4 位（0571、0755）。按首位的字形去猜长度，
    # 好过「先试 4 位」：025-52785866 会被先切成区号 0255。
    lengths = (3, 4) if digits[1:2] in "12" else (4, 3)
    for length in lengths:
        area, rest = digits[:length], digits[length:]
        if not re.fullmatch(r"0[1-9]\d{1,2}", area):
            continue
        if len(rest) in (7, 8):
            return f"{area}-{rest}"
    return ""


def clean_phone(raw: str) -> tuple[str, str]:
    """→ ``(展示号码, 拒绝原因)``。多号码取第一个合法座机/400，手机号一律不收录。

    多号码写法（``0576-87212222；0576-87370888``）很常见，只取第一个即可；
    整串按「能否解析」判会在多号码行上全线判失败，那是把「写法」当成了「数据」。

    「没有号码」和「号码有问题」要分开：``待补`` / ``未公示`` 是清单没给数据，
    写进备注反而是噪声；``0574-8749（尾号待补）`` 是**残号**，必须说明 ——
    否则读者看到电话栏空白，会以为我们漏了，其实来源本身就是残的。
    """
    value = re.sub(r"\s+", "", (raw or "").strip())
    if not value:
        return "", ""
    # 括注里的说明（「尾号待补」）本身就是「这条号码不完整」的证据，先记下来
    had_note = bool(re.search(r"[（(][^）)]*[）)]", value))
    bare = re.sub(r"[（(][^）)]*[）)]", "", value)
    parts = [p for p in re.split(r"[；;、/／,，]+", bare) if p]
    mobile_seen = False
    for part in parts:
        digits = re.sub(r"\D", "", part)
        if MOBILE_SHAPE.fullmatch(digits):
            mobile_seen = True
            continue
        normalized = normalize_landline(part)
        if normalized:
            return normalized, ""
    if mobile_seen:
        # 只有手机号时，说清是「清单只给了手机」还是「有别的但都不是座机」——
        # 前者是清单的缺口，后者是号码本身有问题，处理方式不同
        if len(parts) == 1:
            return "", "官网/来源只公示手机号"
        return "", "仅有个人手机号"
    # 括号外是哨兵（「待补」「未公示」）且没有数字 ⇒ 来源压根没给号码，
    # 原因由调用方从括号里取出来写进备注，这里不算「拒绝了一个号码」。
    stripped = re.sub(r"\s+", "", bare)
    if stripped in PHONE_SENTINELS and not re.search(r"\d", stripped):
        return "", ""
    # 有数字或有括注说明 ⇒ 这条号码是残的（0574-8749（尾号待补）那种），要说明
    return ("", "号码形态不合法或残缺") if (re.search(r"\d", bare) or had_note) else ("", "")


def clean_address(raw: str) -> str:
    """地址只去掉**纯哨兵值**。

    ``杭州市滨江区（具体门牌待补）`` 这种要保留：它如实说明了「到区为止、门牌未取得」，
    比留空更有用；早先按「含『待补』就丢弃」处理，结果连省份都判错了。
    """
    value = re.sub(r"\s+", "", (raw or "").strip())
    if not value or value in ADDRESS_SENTINELS:
        return ""
    bare = re.sub(r"[（(][^）)]*[）)]", "", value)
    return value if ADDRESS_SHAPE.search(bare) or len(bare) >= 6 else ""


# 镇街名 → (市, 县市区, 镇街名)。用于**只给了街道**的底表：
# 临安的规上企业表没有城市/县列，只有「所属街道」，不给这张索引就只能留空。
# 底表里的街道写法与行政区名不一致的情况。目前只有一处：
# 「科技城」是临安数据表对**青山湖科技城**的简称，落到行政区就是青山湖街道
# （骨架的 TOWNS 里登记的是「青山湖街道」）。两者是同一个实体，不能各建一个码。
TOWN_ALIASES: dict[str, str] = {
    "科技城": "青山湖街道",
}


def town_index() -> dict[str, tuple[str, str, str]]:
    """镇街名 → ``(市, 县市区, 镇街名)``。

    依据是骨架里的 ``TOWNS``（镇街 → 县代码）与 ``DISTRICTS``（市代码 → 县列表），
    所以不需要另立对照表，也不会与行政区层漂移。
    用于**只给了街道**的底表（临安规上企业表没有城市/县列）。
    """
    # DISTRICTS 的结构是「市代码 → [(县代码, 县名)]」，先把县代码摊平成一张表
    district_name = {code: name for group in DISTRICTS.values() for code, name in group}
    city_by_code = {code: name for code, name in CITIES}
    out: dict[str, tuple[str, str, str]] = {}
    for _code, town, district_code in TOWNS:
        district = district_name.get(district_code, "")
        city = city_by_code.get(district_code[:4], "")
        if town and district:
            out[town] = (city, district, town)
    return out


def city_of_district(district: str, city_by_code: dict[str, str]) -> str:
    """县名 → 它所属的设区市。判「市 + 县」是不是同一条链时用。

    开发区/新区（「宁波高新区」「滨海新区」）先按 ``DISTRICT_ALIASES`` 归一：
    它们不是国标县，但清单把它们当县市区用，而它们各自属于一个确定的市 ——
    不归一就会把「宁波市 + 宁波高新区」这种正常组合判成「不同链」。
    """
    for city_code, group in DISTRICTS.items():
        for _code, name in group:
            if name == district:
                return city_by_code.get(city_code, "")
    for (alias_city, alias_district), real in DISTRICT_ALIASES.items():
        if alias_district == district:
            return alias_city
    return ""


def town_in_city(town_col: str, city: str) -> bool:
    """这一行的镇街列写的是不是 ``city`` 这个市里的镇街。

    清单的「镇街」列在镇街级/产业带清单里写的是**产区**的镇街，与「地市 / 县市区」
    三列都指向产区；所以「市 + 县 + 镇街」三列互相自洽并不能证明它们在说这家企业。
    但反过来有用：三列**互相矛盾**时（宁波市 + 瑞安市 + 塘下镇），
    可以确定这些列值不是同一家企业自己的所在地，那就一个都不能用。
    """
    entry = town_index().get(TOWN_ALIASES.get(town_col, town_col))
    if entry and entry[0] == city:
        return True
    # 镇街列可能写成多值（「辉埠镇 / 白石镇」）或带半截括号（「(瓯北街道」）：
    # 逐个片段再试一遍，认不出就返回 False（值不可用）。
    for part in re.split(r"[/／、,，;；]", town_col or ""):
        part = part.strip().strip("（(）)")
        entry = town_index().get(TOWN_ALIASES.get(part, part))
        if entry and entry[0] == city:
            return True
    return False


def zhejiang_place_names() -> set[str]:
    names = {name for _, name in CITIES} | {town[1] for town in TOWNS}
    for group in DISTRICTS.values():
        names |= {name for _, name in group}
    return names


# 浙江 11 个设区市的名字（``CITIES`` 的纯名字版）。
# 用途只有一个：清单的「地市」列是不是浙江省内 —— 见 geocode_row 后的那段判断。
ZHEJIANG_CITY_NAMES = frozenset(name for _, name in CITIES)


# 外省地名：与浙江地名**同名**的那些。省级地名对得出来时，就不该再按地址猜浙江 ——
# 「上海市普陀区」会撞上舟山的普陀区，「江苏省丹阳市」会撞上丹阳这个县级市名。
# 这不是理论风险：第五份清单里的对标龙头就是这么被误判成浙江企业的。
FOREIGN_REGIONS = (
    "北京", "天津", "上海", "重庆",
    "河北省", "山西省", "辽宁省", "吉林省", "黑龙江省", "江苏省", "安徽省", "福建省",
    "江西省", "山东省", "河南省", "湖北省", "湖南省", "广东省", "海南省",
    "四川省", "贵州省", "云南省", "陕西省", "甘肃省", "青海省", "台湾",
    "内蒙古", "广西", "西藏", "宁夏", "新疆",
    # 境外（对标龙头里有日本重机、新秀丽这类）
    "日本", "美国", "德国", "韩国", "意大利", "瑞士", "瑞典", "荷兰", "法国", "英国",
    "新加坡", "香港", "澳门", "境外",
)


def zhejiang_city_in(text: str) -> str:
    """地址里出现的浙江设区市名（只认 11 个市）。

    用途只有一个：企业自己的地址只写到市一级（「浙江省温州市」「宁波市国家高新区」）时，
    别把这家浙江企业的地区整格留空 —— 地区停在市级是事实，不是猜测。
    """
    compact = re.sub(r"\s+", "", text or "")
    for name in ZHEJIANG_CITY_NAMES:
        if name in compact:
            return name
    return ""


# 「这一行不是本产区企业，是拿来对标的龙头」的写法。清单在这件事上写法不统一：
# 第五份一律写在「是否本地产区」列（「对标龙头·非本地产区」），产业带清单写在备注
# （「仅作对标」），纺织清单写「非浙江本地块状企业」。三种都要认。
#
# 只认「明确否定归属」的措辞：**不能**用「行业龙头 / 全国龙头」这类描述性说法 ——
# 那会误伤真的产区企业。所以标记里必须带「仅作 / 非 / 系」这种否定词。
BENCHMARK_MARKERS = (
    "仅作对标",
    "作对标龙头",
    "对标龙头",
    "非浙江本地",
    "非浙江企业",
    "非本地产区",
)

# 断言式标记：把它从备注里**删掉**之后还剩下什么。剩下的那些字里出现浙江地名，
# 说明那个地名只来自标记本身，不是清单在说企业所在地。
#
# 为什么需要它：「境外」两个字里含「境」，「境」在浙江地名表里是**境街道**
# （诸暨市的街道）的字头之一 —— 也就是「上城区」会因为「城」而在「日本上城区」
# 这种巧合里被认出来。删掉标记再判一次，就能把这类巧合挡在外面。
BENCHMARK_ASSERT_PREFIXES = tuple(BENCHMARK_MARKERS) + ("境外",)

# 地址列只写了「省级 / 境外地名」时，它其实是**产地标记**而不是地址
# （「日本」「境外」「内蒙古」「安徽」）。判据要三点同时成立，否则会误伤：
# 写的就是这些字、企业名或备注里也有同一个词、这一行没有真地址。
PLACE_MARKERS = {
    "日本": "日本",
    "境外": "境外",
    "内蒙古": "内蒙古",
    "安徽省": "安徽",
    "安徽": "安徽",
}


def benchmark_row(local_scope: str, note: str) -> bool:
    """这一行是「拿来对标的龙头」吗 —— 它的行政列写的是**产区**的位置，不是它自己的。

    判错这一类，后果不是少一格数据，而是把一家企业在页面上的所在地写成另一个地级市
    （宁波均胜挂在瑞安塘下产区名下，照抄列值就会变成「温州市瑞安市」）。

    地址列写成**产地标记**（「日本」「境外」「内蒙古」）的那一类不在这里判：
    它同时还要判「不在浙江」，而那件事需要地址列的原值，见解析循环里的 ``PLACE_MARKERS``。
    """
    if "对标" in local_scope or "非本地" in local_scope:
        return True
    return any(marker in note for marker in BENCHMARK_MARKERS)


def looks_outside_zhejiang(text: str) -> bool:
    """地址看起来在浙江之外吗？

    只认**省级**地名，且必须在浙江地名之前判 —— 否则同名的市级/县级地名会先撞上。
    「江苏省丹阳市」与浙江的丹阳是两个地方，但只看「丹阳」是分不出来的。
    """
    compact = re.sub(r"\s+", "", text or "")
    if not compact:
        return False
    return any(name in compact for name in FOREIGN_REGIONS)


# 外省地址里认市名：一个外省地址至少要**两个**中文字，才可能是市名
# （单字会把「市」本身当名字）。用途只有一处：省外对标龙头的地址若只写到省
# （「北京市」「江苏省苏州市」），得有一个市名可填，否则页面显示不出地区。
OUTSIDE_CITY = re.compile(r"([\u4e00-\u9fa5]{2,8}?)(?:市|自治州|地区|盟)")


def outside_city_in(text: str) -> str:
    """外省地址里出现的市名（含「市/自治州/地区/盟」后缀）；认不出返回空。

    只用于**已经判定在浙江之外**的地址：那种情况下「上海市」「江苏省苏州市」
    里的市名就是企业自己的所在地，不是产区坐标。
    """
    compact = re.sub(r"\s+", "", text or "")
    if not compact:
        return ""
    # 省名本身会被这个正则当成市名（「北京市」→ 北京 + 市），所以先去掉省级前缀
    for province in FOREIGN_REGIONS:
        compact = compact.replace(province + "省", "").replace(province, "", 1)
    found = OUTSIDE_CITY.search(compact)
    if not found:
        return ""
    name = found.group(1)
    # 「浙江省」这类剩余片段不该算市名
    return name + found.group(0)[len(name):]


def locate_in_zhejiang(address: str) -> tuple[str, str]:
    """从地址里认出企业所在的浙江「市 / 县市区」。

    优先匹配「市名 + 县名」相邻的写法（``杭州市余杭区``），避免只按县名匹配时
    在长地址里撞上同名片段。认不出来就返回空 —— 宁可不填，也不猜。

    先判「是不是外省」：浙江有几个地名与外省重名（舟山普陀区 vs 上海普陀区），
    不先排除就会把上海企业挂到舟山的区上，而且看起来完全合理。
    """
    text = re.sub(r"\s+", "", address or "")
    if looks_outside_zhejiang(text):
        return "", ""
    district_names: list[tuple[str, str]] = [
        (name, code) for code, group in DISTRICTS.items() for _, name in group
    ]
    district_names.sort(key=lambda item: -len(item[0]))
    city_by_code = {code: name for code, name in CITIES}

    for district_name, code in district_names:
        city = city_by_code.get(code, "")
        if city and f"{city}{district_name}" in text:
            return city, district_name
    for district_name, code in district_names:
        if district_name in text:
            return city_by_code.get(code, ""), district_name
    return "", ""


# 清单里的产区名 → 已有集群 slug 的**同义映射**。
#
# 同一份产业带在不同清单里名字不同：第五份清单写「塘下汽摩配」，
# 骨架里那条叫「瑞安汽摩配」（同一件事：瑞安塘下镇的汽摩配）。
# 这种情况必须**指向已有档案**，不能新建 —— 建了就是同一个产业带两条档案，
# 企业被拆到两边、页面各显示一半（这个坑上一轮在武义/义乌上踩过一次）。
#
# 只有确定是同一件事时才往里加；拿不准就先不加，让那一行没有集群归属，
# 然后人工判断 —— 挂错集群比不挂更难发现。
CLUSTER_SYNONYMS: dict[str, str] = {
    "塘下汽摩配": "ruian-qimopei",          # 瑞安市塘下镇，与「瑞安汽摩配」同一条
    "崧厦伞业": "shangyu-sanye",            # 上虞区崧厦街道，与「上虞伞业」同一条
    "店口铜加工": "diankou-wujin",          # 诸暨市店口镇，与「店口五金」同一条
    "云和木玩": "yunhe-muzhiwanju",         # 云和县，与「云和木制玩具」同一条
}


# 「区级产业分组」→ 集群 slug。第六份清单（临安区规上企业）用的是这张表。
#
# 为什么不走 CLUSTER_SYNONYMS：那张表是「同一个产业带的另一种叫法」，
# 而这里是「一个区级分组对应我们哪个产地档案」——分组的名字（临安新材料）
# 与档案的名字可能一致，但含义的来路不同，分开写才不会互相污染。
#
# 表里有 14 个分组，只映射 11 个：**太阳五金工具**与**青山湖科技城高端装备**
# 分别对应「五金工具」「高端装备」两个分组，另有「装备制造」分组（68 家）
# 与「复合装饰材料」（56 家）也归到这两条产线上。其余 3 个分组
# （其中高端装备、装备制造、复合装饰材料）是**上位/平行分类**，不单独建档 ——
# 它们的企业按街道落到已有产区上，落不到就不挂（见 dispatch_cluster）。
LINAN_INDUSTRY_CLUSTERS: dict[str, str] = {
    "电线电缆": "linan-dianxian",
    "复合装饰材料": "linan-zhuangshizhi",
    "坚果食品": "linan-shanhetao",
    "照明电器": "linan-jienengdeng",
    "五金工具": "linan-taiyang-wujin",
    "装备制造": "qingshanhu-zhuangbei",
    "其中高端装备": "qingshanhu-zhuangbei",
    "新材料": "linan-xincailiao",
    "纺织服装": "linan-fangzhifuzhuang",
    "电子电路": "linan-dianzidianlu",
    "生物医药": "linan-shengwuyiyao",
    "新能源": "linan-xinnengyuan",
    "集成电路": "linan-jichengdianlu",
    "精密元器件": "linan-jingmiyuanqijian",
}


# 「产区」→ 集群 slug，以及这一行是不是省外产区。
#
# 为什么要有这张表：这份映射在别处已经存在两份 —— ``seed.companies`` 的
# ``PRODUCTION_AREA_CLUSTER``（灌库时按产区列兜底）与 ``tools/parse_seamless.py``
# 的 ``PRODUCTION_AREAS``（首版 19 家的解析脚本）。但**通用导入器**只认
# 「块状经济」那种逐行独立的产区名，读不懂「产区」这一列 —— 于是同一份
# 《全国无缝服装企业调研清单》走接口导入时 137 行全部挂不上集群，
# 而且汕头/佛山/晋江的行会被当成浙江企业计入统计。走接口是本文档推荐的路径，
# 规则就得在这里也齐一份。
#
# 三份表的值必须一致（测试与导入报告都会把它们摆在一起看）。
# 与 ``dispatch_cluster`` 分开写：那张表回答「这一行的产区叫什么名字」，
# 这张表回答「这个名字属于哪个档案、是不是省外」。混在一起会让
# 「临安新材料」这类区级分组也长出省外标记。
PRODUCTION_AREA_INDEX: dict[str, tuple[str, bool]] = {
    # 官方「新星」产业群（行业内官方第四批名单里就有这条）
    "义乌无缝针织": ("newstar-18-无缝服装", False),
    # 省内调研产区：官方名单里没有对应条目，所以在 research_clusters.json 单独建档
    "浦江针织服装": ("pujiang-zhenzhi-fuzhuang", False),
    # 省外对比产区：单独建档、不挂浙江行政区、单独计数
    "汕头谷饶针织内衣": ("shantou-gurao-seamless", True),
    "汕头潮南内衣": ("shantou-chaonan-underwear", True),
    "佛山盐步内衣": ("foshan-yanbu-brands", True),
    "晋江深沪内衣": ("jinjiang-shenhu-underwear", True),
}

# 清单里那些**不是产区**的产区值：它们是「不用挂集群」的写法，不是我们没认出来。
# 分开记的理由和空档位一样 —— 「未识别」该进 warnings，「作者明确写了别处」
# 不该进。混在一起，报告里的警告会长期挂着一条噪声，真正的漏识别反而没人看。
NON_AREA_VALUES = frozenset({"其他产区", "其他", "省外", "—", "-", "/"})


# 「集群类型」列的取值 → 这一行挂到哪个集群。
#
# 这一列回答的是「这行**代表**哪条赛道/哪个产业群」，与「产区」列回答的
# 「这行的产地在哪」是两回事。本库此前只支持前者：``seed.companies.cluster_reps_slug``
# 会读行内的 cluster_type / track / sub_track，而**通用导入器**既不认这三列、
# 也不给记录填 ``track`` —— 于是《第四批产业集群全国代表企业清单》与
# 《现代纺织与服装企业调研清单》这类清单走接口导入时，
# 「集群代表企业」那批行会全部挂不上它们代表的赛道集群。
#
# 表里的值全部来自已建档的集群（415X-<赛道>），与 seed 侧的处理保持同一语义。
CLUSTER_TYPE_INDEX: dict[str, str] = {
    "核心区": "415X-{track}",
    "协同区": "415X-{track}",
}


def cluster_from_type(row: dict, columns: dict[str, str]) -> tuple[str, str]:
    """一行 → ``(集群 slug, 「集群类型」原值)``。

    「集群类型」只在第四批产业群名单语境里有意义（核心区/协同区/新星产业群），
    普通调研清单不带这一列，那就返回空 —— 不猜。

    「新星产业群」不在这里算：它要按「县市区 + 细分赛道」去匹配官方名单，
    那件事 seed 侧已经在做（``seed.companies.cluster_reps_slug``）。
    导入阶段认不出来不代表丢了归属，所以那种取值也不进「未识别」警告。
    """
    kind = _row_value(row, columns, "cluster_type").strip()
    track = _row_value(row, columns, "track").strip()
    if not kind or not track:
        return "", kind
    template = CLUSTER_TYPE_INDEX.get(kind)
    return (template.format(track=track) if template else ""), kind


# 导入阶段不认、但 seed 侧会按「县市区 + 细分赛道」补齐的集群类型。
# 只有这一类「认不出来也不算丢归属」，可以跳过警告 —— 其余的未识别取值必须报出来。
SEED_HANDLED_TYPES = frozenset({"新星产业群"})


def cluster_slug_index() -> dict[str, str]:
    """产业带名 → 集群 slug。三个来源合起来，就是灌库后会建出的全部集群。

    清单只给「块状经济 / 产业带」这类中文名，名字→slug 的对应关系在骨架文件里，
    **不在这里另立一张表** —— 另立一张就会与骨架漂移，而且漂移是静默的
    （企业挂到一个不存在的集群上，只是页面少几家企业，不会报错）。

    四类名字都要进索引，少一类就是「几十行看着导进去了，却不出现在任何集群页」：

    1. ``clusters_seed.json`` 的 ``clusters``（88 个镇街级产区）；
    2. ``research_clusters.json``（调研派生产区）——档案名带我们的括注
       （「浦江针织服装（省内调研产区）」），而清单里写的是裸名，
       所以**整名与剥括注后的裸名都进**；
    3. ``clusters_seed.json`` 的 ``tracks``（415X 赛道，slug = ``415X-<赛道>``）。
       2026-10-21 的《浙江省产业带企业调研清单》里 146 行写的就是赛道名
       （智能物联 / 高端软件 / 集成电路…），漏掉这一组就整批落空；
    4. 第四批官方名单里的 22 个「新星」产业群。它们在灌库时按
       ``{县市区}{产业群}``（如「临安区创新药与医疗器械」）命名，
       所以索引键必须用同样的拼法 —— **不要加裸名**：
       4 个县都有「光电（半导体）」，裸名只能指向其中一个，那是错的。
    """
    index: dict[str, str] = {}
    for filename in ("clusters_seed.json", "research_clusters.json"):
        path = settings.curated_dir / filename
        if not path.exists():
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        for row in payload.get("clusters", []):
            slug, name = row.get("slug"), row.get("name")
            if not slug or not name:
                continue
            index.setdefault(name, slug)
            bare = re.sub(r"（[^）]*）$", "", name).strip()
            if bare:
                index.setdefault(bare, slug)
        # 415X 赛道：slug 就是 415X-<赛道名>，名字与 slug 都能当键
        for row in payload.get("tracks", []):
            slug, name = row.get("slug"), row.get("name")
            if slug and name:
                index.setdefault(name, slug)

    # 「新星」产业群：与 seed.loader.seed_newstar_clusters 的命名规则必须一致
    newstar = settings.source_dir / "zhejiang_clusters_batch4.json"
    if newstar.exists():
        try:
            payload = json.loads(newstar.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            payload = {}
        for row in payload.get("新星产业群", []):
            slug = f"newstar-{row['seq']:02d}-{row['cluster']}"
            index.setdefault(f"{row['district']}{row['cluster']}", slug)
    return index


def production_area_of(row: dict, columns: dict[str, str]) -> tuple[str, str, bool]:
    """一行 → ``(产区名, 集群 slug, 是否省外产区)``。

    「产区」这一列在《全国无缝服装企业调研清单》里是**清单自己给出的归属依据**，
    比按地址/产品猜要准（佛山盐步那家企业在南海区，本库的地理层是浙江的，
    猜不出任何东西来）。认不出来的产区值原样返回、slug 留空 ——
    报告会把它连同行数列出来（``unknown_production_areas``），
    这样新增一个产区时不会静默丢归属。
    """
    value = _row_value(row, columns, "production_area").strip()
    if not value:
        return "", "", False
    if value in NON_AREA_VALUES:
        # 「其他产区」：作者自己说这一行的产区不在这份清单的枚举里。
        # 不是我们没认出来，所以不算未识别 —— 但也不挂集群（挂错比不挂更难发现）。
        return value, "", False
    slug, out_of_province = PRODUCTION_AREA_INDEX.get(value, ("", False))
    return value, slug, out_of_province


def dispatch_cluster(name: str, index: dict[str, str], basis: str = "") -> str:
    """一行 → 它所属的集群 slug。

    判据按可信度排序，顺序不能反：

    1. **等价名**（``CLUSTER_SYNONYMS``）：同一件事的两种叫法 → 指向已有档案；
    2. **区级产业分组**（``LINAN_INDUSTRY_CLUSTERS``）——**只在依据是
       「区级产业分组」时启用**；
    3. **名字直配**（``cluster_slug_index``）：骨架里的集群名。

    全都配不上就返回空 —— 宁可让这一行没有集群归属，也不要猜一个挂上去。

    第 2 条为什么必须限定依据：那张表是**临安区规上企业底表**的分组名，
    它自己的注释就写着「分组的名字（临安新材料）与档案的名字可能一致，
    但含义的来路不同，分开写才不会互相污染」—— 而不带条件地查它，
    恰恰就是污染。实测：《浙江省产业带企业调研清单》的「产业带」列写着
    「集成电路」（指 415X 赛道，24 行分布在萧山/北仑/富阳/滨江/越城），
    却被这张表截走、挂到临安集成电路（临安区那 2 家真正的临安企业中，
    另有一行写「临安集成电路」）。
    归属错了比没有归属更难发现，所以这里按「这一列是什么」来放行。
    """
    # 有些导出（如临安规上企业底表）会在枚举值尾部留一个拼接逗号（``"1.1,"``）。
    # 归一化放在这里而不是调用处：调用处每多一个，就多一次「忘了写」的机会。
    name = (name or "").strip().rstrip(",，、;；")
    if not name:
        return ""
    if name in CLUSTER_SYNONYMS:
        return CLUSTER_SYNONYMS[name]
    # 区级产业分组：名字太宽（「集成电路」「新材料」「装备制造」），
    # 只有明确来自那一列时才认，否则会截走别处同名的赛道/产业带。
    if basis == "industry_group" and name in LINAN_INDUSTRY_CLUSTERS:
        return LINAN_INDUSTRY_CLUSTERS[name]
    return index.get(name, "")


def geocode_row(
    row: dict,
    columns: dict[str, str],
    raw_address: str,
    local_scope: str,
    note: str,
    zj_names: set[str],
    benchmark: bool = False,
) -> tuple[str, str, str, bool]:
    """一行 → ``(市, 县市区, 是否省外)``。

    两类清单需要用两种判据，混用会出错：

    * 第四批「全国代表企业」清单没有行政列，而且它的「集群（县市区）」
      指的是**集群**的位置 —— 科大讯飞挂在余杭的集群上，人在合肥。
      那种清单只能靠**企业自己的地址**反推。
    * 镇街级清单**逐行给了地市/县市区**，而它的地址常常不含县名
      （「壶山街道黄龙工业区」）。这种清单信列值。

    所以判据是「有没有行政列」：有列就信列（地址只在列缺失时才用），
    没列才完全按地址反推。省外用清单自己写的「对标龙头 / 非本地」标记。

    两个会推翻上面判据的情形，都由调用方显式传进来：

    * ``benchmark`` —— 清单说这一行「仅作对标 / 非浙江本地」，它的行政列写的是
      **本地产区**的位置（宁波均胜挂在瑞安塘下产区名下，地址写的是「瑞安市」），
      照抄就会把这家宁波公司标成温州企业。
      地址列写成 ``PLACE_MARKERS`` 那种产地标记（「日本」「境外」「内蒙古」）的行
      也走这一条：它同样不是这家企业的地址，而「不在浙江」由
      ``looks_outside_zhejiang`` 从那个标记里读出来（它认省级地名）。
    """
    district_col = _row_value(row, columns, "district")
    named_city = _row_value(row, columns, "city")

    # 清单自己说这一行不在本地产区（对标龙头、非浙江本地）。这一列比任何推断
    # 都可信 —— 它区分的是「这家龙头企业是不是这个镇街的」，与「地址在不在浙江」
    # 无关（天堂伞在萧山、均胜在宁波，都是浙江企业，只是不在那个产区）。
    if benchmark or "对标" in local_scope or "非本地" in local_scope:
        # 这一行的行政列写的是**本地产区**的位置，不是这家企业自己的 ——
        # 对标龙头是拿来比的，人在别处（宁波均胜挂在瑞安塘下产区名下）。
        # 所以它的所在地只能从自己的地址认；认不出就留空，宁可不填。
        #
        # 但地址与备注里可能是**产地标记**而不是地名（「日本」「境外」「内蒙古」），
        # 那些标记要先从文本里删掉再判浙江地名。理由有两层：
        #   · 「境外」的「境」是浙江地名「境街道」的字头之一，而「上城区」的
        #     「城」又落在**上城**这个词里 —— 不删标记，`新秀丽（境外）`
        #     会被认成杭州上城区的企业，再挂上产区的县市区；
        #   · 标记本身在说这一行取自哪里，不是企业在哪。
        # 去掉标记后剩下的文本里还有浙江地名，才是「企业自己在浙江」。
        bare_address = raw_address
        bare_note = note
        for marker in BENCHMARK_ASSERT_PREFIXES:
            bare_address = bare_address.replace(marker, "")
            bare_note = bare_note.replace(marker, "")
        # 删标记会连「境外」里含的那个地名一起删掉，所以判浙江地名要用原文；
        # 但只有当**原文颁去标记之后**还认得出浙江地名时才算数。
        city, district = locate_in_zhejiang(raw_address)
        if city and not (locate_in_zhejiang(bare_address)[0] or zhejiang_city_in(bare_address)):
            city, district = "", ""
        if not city:
            city = zhejiang_city_in(raw_address)
            if city and not zhejiang_city_in(bare_address):
                city = ""
        # 地址判不出来时看备注（「｜日本」这种标记写的正是它的出处）
        if not city:
            city, district = locate_in_zhejiang(bare_note)
        if city:
            return city, district, False
        if looks_outside_zhejiang(raw_address or note):
            # 外省 / 境外的对标龙头。地址只写到省市（「北京市」「江苏省苏州市」）
            # 时按地址认不出市名，但清单的「地市」列对这类行是**它自己**的所在地 ——
            # 这些企业根本不在浙江，产区坐标只会写在县市区那一列，所以地市可以采信，
            # 页面才显示得出「苏州市」。
            #
            # 县市区一律不采信：那一列至少一半写的是产区所在的县
            # （立讯的「乐清市」、罗莱的「海宁市」、探路者的「三门县」）。
            #
            # 地市若恰好是浙江的市，说明这一列写的仍是产区 —— 留空
            # （JUKI 的地址是「日本」，地市列却是「台州市」）。
            if named_city and named_city not in ZHEJIANG_CITY_NAMES:
                return named_city, "", True
            # 地市列也是产区（或没写）：从**外省地址本身**取市名 ——
            # 「江苏省苏州市」里的苏州市是企业自己的所在地，比产区坐标可信。
            return outside_city_in(raw_address), "", True
        # 既认不出浙江、地址也不像省外：这一行是浙江企业，但清单的列值只给得出
        # 产区位置（均胜在宁波、森马/人本在温州，列值却是瑞安/常山）。
        # 那就留空 —— 地区由「其余名单里已确认的值」在合并时补上。
        return "", "", False

    if district_col:
        # 有市也有县：信列。地址只在列没给县、而地址能给出县时覆盖
        # （企业的地址常常比清单更细）。
        city, district = locate_in_zhejiang(raw_address)
        if city in ZHEJIANG_CITY_NAMES and named_city and named_city not in ZHEJIANG_CITY_NAMES:
            # 清单的「地市」列写的是**产区**所在的外省市（舟山的水产企业
            # 挂在「上海市普陀区」下面 —— 那是舟山渔业的对标产区坐标），
            # 而企业自己的地址落在浙江。地址说的是这家企业，列值说的是产区，
            # 所以按地址算，并且不因此把它算成省外企业。
            return city, district, False
        if not district_col and district:
            district_col = district
        if district_col and named_city:
            if named_city not in ZHEJIANG_CITY_NAMES:
                # 外省行：`DISTRICTS` 里没有外省的县，判不了链 —— 列值原样用。
                return named_city, district_col, False
            # 浙江行：县与市必须同属一条链。挂着「宁波市 + 瑞安市」的都不对 ——
            # 瑞安市在温州。这种情况只按**镇街列**判一次（它给的产区镇街不在
            # 这个市就说明市列也是产区坐标），镇街给不出可用的线索时留空，
            # 地区由其余名单里已确认的值在合并时补上。
            if city_of_district(district_col, {c: n for c, n in CITIES}) == named_city:
                return named_city, district_col, False
            return (named_city, district_col, False) if town_in_city(
                _row_value(row, columns, "town"), named_city
            ) else ("", "", False)
        return named_city or city, district_col, False

    # 有市无县（这一行的行政列只写到市）：地址能给出县就用地址的，
    # 给不出就试镇街列 —— 「良渚街道」能反查到余杭区，「（瓯北街道」
    # 这种半截写法反查不到就老实留空，不猜。
    if named_city:
        city, district = locate_in_zhejiang(raw_address)
        if district:
            return named_city or city, district, False

    # 只有「所属街道」这一类底表：从镇街反推市与县市区（依据骨架的行政区层，
    # 不另立对照表）。这样底表也能给出地区和产区归属，不必去猜。
    town_col = _row_value(row, columns, "town")
    if town_col:
        found = town_index().get(TOWN_ALIASES.get(town_col, town_col))
        # 镇街反查出来的市必须与这一行的「地市」列一致。不一致说明这个镇街是
        # **产区**的镇街，不是企业注册地的镇街 —— 森马/巴拉巴拉挂在织里童装产区下，
        # 镇街列写「织里镇」，照它反查就成了「湖州市吴兴区」，可这家企业在温州。
        # 镇街反查只在两种情况下可信：这一行没有市列，或者反查结果与市列同市。
        if found and (not named_city or found[0] == named_city):
            return found[0], found[1], False
        if not found and not named_city:
            # 街道认不出来就留空 —— 不猜。但报告里会列出来（见 report.unresolved_towns）
            return "", "", False

    if named_city:
        # 县给不出（地址只到市、镇街也认不出）：至少把市留住 ——
        # 地区停在市级是事实，不是猜测（与 ``zhejiang_city_in`` 同一条理由）。
        return named_city, "", False

    # 没有行政列（全国/跨省清单）：只能靠地址
    # 「地址」列偶尔写的是**注释**而不是地址（「仅作对标（玉环阀门集群同用此对标）」、
    # 电话线索、定位描述）。按文本特征排掉：真地址一定带行政区划名或地址字
    # （路/街/镇/乡/号/村/园/区/县/市），注释不会。`clean_address` 保存地址时用的
    # 是同一套判据，这里要在**取市名之前**再判一次 —— 否则会把注释里的地级市名
    # 当成企业所在地（「湖州安达」的地址列写的是「浙江湖州」的定位描述）。
    address_like = bool(raw_address) and bool(
        re.search(r"[省市区县]", raw_address) or re.search(r"[路街镇乡号村园]", raw_address)
    )

    if raw_address and not any(place in raw_address for place in zj_names):
        # 例外：「台州湾集聚区」「龙港包装印刷工业园区」这类地址**不含市名也不含县名**，
        # 但备注里常写「本地核心龙头」——这是清单自己给的本地标记，采信它。
        # 这是清单自带的事实，不是我的推断，所以比地址更可信。
        if "本地" in note:
            city, district = locate_in_zhejiang(note)
            return city, district, False
        # 省外行：市名从**外省地址本身**取（「江苏省苏州市」→ 苏州市）。
        # 清单的「地市」列这里不能用 —— 它是产区所在的外省市
        # （施耐德挂在上海、立讯挂在东莞，都不是企业自己）。
        # 县一级不要：这里能走到「不是浙江地名」的地址，多数是清单把**产区**
        # 写进了地址（「普陀区沈家门街道」其实是舟山的普陀区，被上海那个同名
        # 省级地名判成了省外），按它取县就会造出「上海市普陀区」这种公司。
        return outside_city_in(raw_address) if address_like else "", "", True
    city, district = locate_in_zhejiang(raw_address)
    if not city:
        # 地址认不出浙江：这一行是「省级地名判成了省外」的浙江企业
        # （上面的重名坑），或者是真的外省企业。清单的「地市」列能分清这两者 ——
        # 肇庆/邢台的市列是它自己的所在地，写浙江产区的那些不是。
        # 县一级仍然不要（那一列在产区清单里是产区坐标）。
        if named_city and named_city not in ZHEJIANG_CITY_NAMES:
            return named_city, "", not address_like
        # 市列也是浙江的（或不给）：说明列值写的是产区，不是这家企业
        return "", "", False
    if district:
        return city, district, False
    # 地址只认到市一级（「浙江省温州市」）或地址不像地址：信地址的市，县留空 ——
    # 但前提是「地市」列**没有**指向另一个浙江的市（那说明列值是产区坐标）。
    if named_city and named_city in ZHEJIANG_CITY_NAMES and named_city != city:
        return "", "", False
    return city, "", False


def tier_label(tier: str, grade: str, overrides: dict[str, str] | None = None) -> str:
    """一行记录的逐行标签（``第三方调研 · 官网公示（未复核）``）。"""
    if overrides and tier in overrides:
        return overrides[tier]
    prefix = GRADE_PREFIX.get(grade, "外部来源")
    if tier in TIER_LETTERS:
        return f"{prefix} · {TIER_LETTERS[tier]}"
    for keyword, detail in TIER_DETAILS:
        if keyword in tier:
            return f"{prefix} · {detail}"
    # 空档（「—」）常见于**对标龙头**行：它们不是产区企业，清单不给产区档位。
    # 这是清单在说「这一行没有档位」，不是我们识别失败，所以不打警告。
    if tier in ("—", "-", ""):
        return f"{prefix} · 未标档位"
    # 认不出就写清楚「未复核」，不猜它的含义 —— 但要在报告里报出来（见 warnings）
    return f"{prefix} · 未复核"


def _row_value(row: dict, columns: dict[str, str], key: str, default: str = "") -> str:
    column = columns.get(key)
    if not column:
        return default
    # 原值原样返回。「—」这类破折号**不是**通用的「空」：
    # 镇街级清单的「核实等级」就用「—」表示「对标龙头、未标档位」，
    # 归一成空串会让那一档消失。真正需要区分「有没有值」的地方
    # 用下面的 is_blank() 单独判断。
    return (row.get(column) or "").strip()


# 原始行留档时要**排除**的列：它们是人信息，即使来源已打码也不存。
# 白名单式排除比「记得别存」可靠 —— 底表换一版多出个「联系电话」列时，
# 这条规则会自然地把它挡住（前提是列名要能被认出来）。
PERSONAL_COLUMNS = frozenset({
    "法人姓名", "法定代表人", "负责人", "联系人", "联系电话", "手机号",
    "组织机构代码", "创建人", "更新人",
})


# 只有这些取值是明确的「空」。不含「—」：那要看具体是哪一列。
BLANK_VALUES = frozenset({"", "N/A", "null", "NULL"})


def is_blank(value: str) -> bool:
    """这一列是不是「没有值」？

    官网类型/地址这类列里，``—`` 与 ``-`` 是「没有」的常见写法；
    但核实等级那种列里 ``—`` 是有效取值。所以判空只在**调用处**按列决定，
    不做全局归一化。
    """
    return (value or "").strip() in BLANK_VALUES


def parse_list_csv(
    text: str,
    *,
    list_name: str,
    grade: str,
    source_file: str = "",
    tier_overrides: dict[str, str] | None = None,
    default_cluster_slug: str = "",
    source_key: str = "",
) -> dict:
    """解析一份名单 CSV → 证据行 / 联系方式层 / 候选官网层 + 报告。

    解析规则与清单无关：列名靠别名认，档位文案靠关键词分类，
    企业所在地从**它自己的地址**认（不能用「集群（县市区）」——那是集群的位置，
    全国代表企业经常不在那儿：科大讯飞挂在余杭的集群上，人在合肥）。
    """
    reader = csv.DictReader(io.StringIO(text))
    header = [h.strip() for h in (reader.fieldnames or [])]
    columns = resolve_columns(header)
    if "name" not in columns:
        raise ValueError(
            f"缺少企业名称列（可用的列名：{'、'.join(COLUMN_ALIASES['name'])}）；实际列：{header}"
        )

    rows_in = [{k.strip(): (v or "") for k, v in row.items() if k} for row in reader]
    if not rows_in:
        raise ValueError("CSV 没有数据行")

    zj_names = zhejiang_place_names()
    # 跨名单对照只在**确实在修订已登记名单**时才做：`parse_list_csv` 是纯函数，
# 导入接口之外还有干跑预览与测试在调它，没有 source_key 的调用不该去读一遍
# 全库骨架（那是每个调用都要付的代价，也让它不再是「只解析这一份」的函数）。
    inherited: dict[str, tuple[str, str]] = {}
    if source_key:
        from ..seed.companies import load_company_skeletons

        inherited = {
            c["name"]: (c.get("city") or "", c.get("district") or "")
            for c in load_company_skeletons(exclude_source_key=source_key)
            if c.get("city") or c.get("district")
        }
    # 集群名 → slug。这份索引只在清单带「块状经济」这类列时才用得上，
    # 读不到就是空表（那时 cluster_slug 一律为空，不会挂错）。
    cluster_index = cluster_slug_index() if "cluster_name" in columns else {}
    records: list[dict] = []
    missing: list[int] = []
    mobile_rejected: list[str] = []
    third_party: list[str] = []
    unknown_kinds: set[str] = set()
    unknown_tiers: set[str] = set()
    # 认不出来的「产区」值 → 行数。新增一个产区时它不该静默丢归属。
    unknown_areas: dict[str, int] = {}
    # 认不出来的「集群类型」值 → 行数（同一件事，另一列）
    unknown_cluster_types: dict[str, int] = {}
    excluded: list[dict] = []
    unresolved_towns: set[str] = set()

    for index, row in enumerate(rows_in, 2):  # 2 = 表头占第 1 行
        name = _row_value(row, columns, "name")
        if not name:
            missing.append(index)
            continue

        # 政府底表会留着已注销的企业（它当年在库）。这类行不能收录 ——
        # 「规上企业底表里有」不等于「它现在还在经营」。排除，但要在报告里点名。
        status = _row_value(row, columns, "operating_status")
        if status and "在营" not in status and "开业" not in status:
            excluded.append({"name": name, "status": status, "reason": "经营状态不是「在营」"})
            continue

        tier = _row_value(row, columns, "verify_tier")
        kind = _row_value(row, columns, "website_kind")
        raw_site = _row_value(row, columns, "website")
        raw_phone = _row_value(row, columns, "phone")
        # 镇街：清单的「镇街」列可能写成「虎山街道 / 贺村镇」这种多值，
        # 原样保留（它是产地范围，不是单一实体）
        town_col = _row_value(row, columns, "town")
        note_parts = [p for p in [_row_value(row, columns, "note")] if p]
        # 有些清单用「地址」列写**对标龙头的来源**（「仅作对标（玉环阀门集群同用此对标）」），
        # 那类内容不是地址，别让它只在备注为空时才出现。判据很简单：不像地址的地址。
        address_cell = _row_value(row, columns, "address")
        # 「地址」列偶尔写的是**注释**而不是地址（「仅作对标（玉环阀门集群同用此对标）」）。
        # 判据要小心：字符集里不能有「市」「区」「县」本身，否则「浦江县」这种
        # 只写了县名的**真地址**会被当成注释挪进备注，地址字段就空了。
        # 所以只认真正像注释的形态：没有行政区划名，也不带后续的地址字。
        if address_cell and not re.search(r"[省市区县]", address_cell) and not re.search(
            r"[路街镇乡号村园]", address_cell
        ):
            note_parts.append(address_cell)
            address_cell = ""

        # 「是否本地产区」这一列不只是「在不在浙江」：它区分三种情况，
        # 而页面上要能看出是哪一种 —— 只写「省外」会让浙江的龙头企业被误读成外省公司。
        local_scope = _row_value(row, columns, "local_scope")
        raw_address = re.sub(r"\s+", "", address_cell)
        benchmark = benchmark_row(local_scope, "｜".join(note_parts))
        # 地址列写的是省级/境外地名（「日本」「境外」「内蒙古」）时，那一格其实是
        # 清单的**产地标记**：它同时也说明这一行不在浙江。两个判断都在这里做，
        # 所以它们是相邻的两句 —— 分开写就会漏掉其中一个（漏掉 benchmark 会让
        # 产区坐标被当成企业所在地，漏掉 out_of_province 会把境外企业算进浙江）。
        place_marker = PLACE_MARKERS.get(raw_address)
        marker_in_note = bool(place_marker) and place_marker in "｜".join(note_parts)
        benchmark = benchmark or marker_in_note
        if benchmark:
            note_parts.append(f"清单标注：{local_scope or '仅作对标'}（不作为该产区企业统计）")

        url = clean_website(raw_site)
        address = clean_address(raw_address)
        phone, phone_reject = clean_phone(raw_phone)
        if phone_reject:
            mobile_rejected.append(name)
            note_parts.append(f"电话未收录：{phone_reject}（原文「{raw_phone}」）")
        elif not phone:
            # 没号码但**写明了为什么**（「待补（官网公示号码被打码）」）。
            # 括号外那部分是哨兵，括号里那句才是信息 —— 丢掉它，
            # 读者看到空白只会以为是我们漏填。
            _, why = split_sentinel_note(raw_phone)
            if why:
                note_parts.append(f"电话待补：{why}")

        # 「官网类型」这一列决定链接能不能进官网字段。
        #
        # 清单**没有**这一列（kind 与网址都空）时，直接按「有链接就是官网」处理：
        # 这时清单没有对链接性质做任何声明，我们不能替它声明，但也不该把它
        # 当成一个「未识别的新类型」——那会让整份清单白报一条警告，
        # 而真正的「经销商站」这种新类型会淹没在这条噪声里。
        # 「这一列有值吗」按本列的口径判断：官网类型列里 `—`/`-` 都是「没有」，
        # 而核实等级列里 `—` 是有效档位 —— 所以判空只在这里做。
        kind = "" if kind in ("—", "-", "无") else kind
        has_kind_column = bool(columns.get("website_kind"))
        candidate = ""  # 只作候选（不发布），可送进验收门
        if not has_kind_column:
            # 没有「官网类型」这一列：清单没有对链接性质做任何声明。
            # 有链接就按企业官网收（我们从没替它声明过别的），没链接就什么都不做。
            website = url
            if not url:
                kind = ""  # 清掉，避免掉进下面的「未识别类型」分支
        elif kind in NO_SITE_KINDS:
            website = ""
            if url:
                # 裸域名 + 「待核/待补」= 作者只对**是不是这家企业的站**没把握，
                # 那就别替它下结论：不发布，但当候选送进本站验收门去核。
                candidate = url
                note_parts.append(f"官网类型为「{kind}」，链接仅作候选（未核实）")
            elif raw_site:
                lead = sentinel_lead(raw_site)
                if lead:
                    note_parts.append(f"官网待补，清单给的线索：{lead}")
        elif kind in NOT_SELF_KINDS:
            website = ""
            if url:
                third_party.append(name)
            note_parts.append(f"官网类型为「{kind}」，按规则不计入企业官网")
            if url:
                note_parts.append(f"参考链接：{url}")
        elif kind in KNOWN_KINDS:
            website = url
        else:
            # 没见过的类型：宁可留空 + 报 warning，也不猜它是不是工厂自己的站
            website = ""
            unknown_kinds.add(kind)
            note_parts.append(f"官网类型为「{kind}」（本站未识别），链接仅作候选")
            if url:
                note_parts.append(f"参考链接：{url}")
        note = "｜".join(note_parts)

        city, district, out_of_province = geocode_row(
            row, columns, raw_address, local_scope, note, zj_names, benchmark
        )
        # 名录里已经确认过的地区先留着 —— `load_company_skeletons` 会用**其余名单**
        # （含已收录的官方名单与其它调研清单）填好这一格。这份清单没给出县一级的值时
        # （行政列空、地址认不出、这一行本就是「拿来对标的龙头」），别把已经确定的
        # 地区清掉：清掉不是「更准确」，而是把已知事实换成空白。
        #
        # 只在**自己没给出县**时才过继：自己给出了县就是自己更具体，
        # 拿别处的值盖掉等于用劣于事实的信息替换事实（中银电池的「江北区」
        # 差点被别处那条「宁波高新区」盖掉）。
        #
        # 也不在「这一行是拿来对标的龙头」时过继：那种行的地名在别处，
        # 但地区得由它自己的地址决定（见 geocode_row 的同一条判断）。
        _inherited_city, _inherited_district = inherited.get(name, ("", ""))
        if not benchmark and _inherited_city and not district:
            city, district = _inherited_city, _inherited_district
            # 过继来的地区只负责**把地区填上**，不负责判定省内外 ——
            # 那不是这份清单里的事实，而是别处清单里的：这一行如果清单自己说了
            # 是省外（地址取不到值时的保守判断），过继来的浙江地区会让它
            # 变成「浙江企业」。省外这一档只有这份清单自己能撤销，
            # 而它撤销的判据是「地址里认出浙江地名」（geocode_row 里已判完）。
            if out_of_province:
                out_of_province = _inherited_city not in ZHEJIANG_CITY_NAMES
        raw_town = _row_value(row, columns, "town")
        if raw_town and not city and not district:
            unresolved_towns.add(raw_town)
        # 归属依据按可信度取第一个可用的：
        #   块状经济列（镇街级清单逐行写明的产区）
        #   → 产区列（第四批代表企业清单）
        #   → 区级产业分组（政府底表，最粗的一档，只做兜底）
        # 记下**用了哪一列**，页面才能如实说明这个归属是怎么来的。
        #
        # 依据同时记两个值：
        #   `cluster_source` 是**规范列名**，只供逻辑判断（LINAN_INDUSTRY_CLUSTERS
        #     只对「区级产业分组」放行，见 dispatch_cluster）；
        #   `cluster_basis` 是给人和页面看的**实际列名**（「产业带」「块状经济」…）——
        #     这份清单的列叫「产业带」，若一律显示「块状经济」，读者会以为
        #     我们认错了列，也就无从判断这个归属该不该信。
        cluster_key = ""
        cluster_source = ""
        cluster_basis = ""
        for key, fallback_label in (
            ("cluster_name", "块状经济"),
            ("cluster_district", "产区"),
            ("industry_group", "区级产业分组"),
        ):
            value = _row_value(row, columns, key)
            if value:
                cluster_key, cluster_source = value, key
                cluster_basis = columns.get(key) or fallback_label
                break
        cluster_slug = dispatch_cluster(cluster_key, cluster_index, cluster_source)

        # 「产区」列（全国性清单：一份清单里好几个产区）走另一套映射 ——
        # dispatch_cluster 认的是**逐行独立的产区名**，而这一列的取值是
        # 「义乌无缝针织」「汕头谷饶针织内衣」这种「地方 + 品类」，还带省内外之分。
        # 这列认得出来时以它为准；认不出来只记账（见 unknown_production_areas），
        # 不动上面按「产区」粗列 / 区级分组算出来的结果。
        area_name = ""
        if columns.get("production_area"):
            area_name, area_slug, area_out_of_province = production_area_of(row, columns)
            if area_slug:
                cluster_slug = area_slug
                cluster_basis = "清单产区列"
            elif area_name and area_name not in NON_AREA_VALUES:
                unknown_areas[area_name] = unknown_areas.get(area_name, 0) + 1

        # 「集群类型」列（第四批产业群名单语境）：核心区/协同区 + 赛道 → 415X-<赛道>。
        # 放在最后，因为这一列只在这类清单上出现；一旦出现，它比「块状经济」列更准
        # （前者说的是「这行代表哪条赛道」，后者说的是「这行属于哪个镇街产区」，
        # 两者可以同时出现在一行上）。
        type_slug, type_kind = cluster_from_type(row, columns)
        if type_slug:
            cluster_slug = type_slug
            cluster_basis = f"清单集群类型（{type_kind}）"
        elif type_kind and type_kind not in SEED_HANDLED_TYPES:
            # 认不出来的集群类型：可能拼成了一个不存在的 slug，不能静默挂空
            unknown_cluster_types[type_kind] = unknown_cluster_types.get(type_kind, 0) + 1

        # 整份清单的归属声明：兜底，不是覆盖 —— 逐行自带的依据（块状经济列、
        # 产区列、集群类型列）都比「整份清单都在这一条赛道上」更具体。
        # 它写进注册表（``default_cluster``），所以「这 317 行为什么都挂这条赛道」
        # 在页面的来源声明里说得清，而不是只存在于某次导入的上下文里。
        if not cluster_slug and default_cluster_slug:
            cluster_slug = default_cluster_slug
            cluster_basis = "整份清单归属"

        # 省外产区：**地区照留**（汕头市/潮南区是事实，藏着只会让企业页没有地区），
        # 但 `out_of_province` 必须是真的 —— 本库是浙江产业带库，
        # 把省外样本混进「浙江企业数」等于换个方式说谎。
        # 不担心它被挂上浙江行政区：loader 对 region_source=out_of_province 的行
        # 既不 match_region 也不按名称推断（``seed_enterprises`` 那道判断）。
        if columns.get("production_area") and area_out_of_province:
            out_of_province = True

        # 清单没标省外时，省外只能从**地市名**认（「揭阳市」「苏州市」）。
        #
        # geocode_row 的判据是「有行政列就信列」，而它判断「是不是浙江」用的是
        # 地址文本 —— 这份清单的地址栏写的是企业门牌（「钟潭竹园93号」），
        # 不含省市名，于是外省地市一路顺风地被当成浙江省内。
        # 「其他产区」那 7 行里就有两行是这么漏的（广东普宁、江苏昆山）。
        if not out_of_province and city and city not in ZHEJIANG_CITY_NAMES:
            out_of_province = True

        # 成立年份：优先用显式的年份列，否则从「成立日期」里取前四位。
        # 政府底表的成立日期是 ``1997-01-28``，直接截年份即可 —— 不解析日期，
        # 因为我们只需要年份，解析只会引入时区/格式的额外失败面。
        # 成立年份可能是「2020」这种裸年份，也可能是「1997-01-28」这种完整日期。
        # 两种都从同一个键取（清单里这一列叫「成立日期」或「成立年份」），
        # 所以这里统一按「取前四位数字」处理，不做日期解析 ——
        # 我们只要年份，解析日期只会引入格式与时区的额外失败面。
        found_raw = _row_value(row, columns, "founded_year") or _row_value(row, columns, "established")
        found_year = found_raw[:4] if re.match(r"^\d{4}", found_raw) else ""

        seq_raw = _row_value(row, columns, "seq")
        records.append(
            {
                "list_name": list_name,
                "seq": int(seq_raw) if seq_raw.isdigit() else len(records) + 1,
                **({"batch": _row_value(row, columns, "batch")} if columns.get("batch") else {}),
                "name": name,
                "match_name": match_name_of(name),
                "product": _row_value(row, columns, "product"),
                # 政府底表带的行业信息：它是产区归属的**依据**，必须留档，
                # 否则以后没人说得清「这家为什么挂在临安电线电缆下」。
                "industry_name": _row_value(row, columns, "industry_name"),
                "industry_code": _row_value(row, columns, "industry_code"),
                "industry_group": _row_value(row, columns, "industry_group").rstrip(",，"),
                # 主体识别信息：政府底表用它做唯一键，我们也该留着
                "credit_code": _row_value(row, columns, "credit_code"),
                "founded_year": int(found_year) if found_year.isdigit() else None,
                "cluster_type": _row_value(row, columns, "cluster_type"),
                "cluster_district": _row_value(row, columns, "cluster_district"),
                "track": _row_value(row, columns, "track"),
                "sub_track": _row_value(row, columns, "sub_track"),
                "verify_tier": tier,
                "website": website,
                # 没有官网但清单给了候选域名（「待核」那种）：不进 website，进候选层
                "candidate_website": candidate,
                "website_kind": kind if (website or kind in NOT_SELF_KINDS) else "",
                "phone": phone,
                "phone_nature": _row_value(row, columns, "phone_nature"),
                "address_type": _row_value(row, columns, "address_type"),
                "email": _row_value(row, columns, "email").lower(),
                "address": address,
                # 企业所在地：清单的行政列优先，地址兜底（见 geocode_row）
                "city": city,
                "district": district,
                "town": town_col,
                # 清单自己声明「这一行属于哪个产区」时，它比按地址/产品推断都准
                "list_cluster_slug": cluster_slug,
                "list_cluster_basis": cluster_basis if cluster_slug else "",
                # 归属依据的**原文**（「临安区创新药与医疗器械」「柳市低压电器」…）。
                # 导入报告要按它分组，才能说清「这 28 行是按产业带列落到哪条档案」；
                # 只留 slug 的话，报告里只剩一串 newstar-14 之类的机器名。
                "list_cluster_name": cluster_key,
                # 「产区」列的原值。「清单产区列」这个依据说的是「按产区列挂的」，
                # 但没说是**哪个**产区 —— 页面与报告要用原值原文，所以单独留一列。
                **({"production_area": area_name} if columns.get("production_area") else {}),
                "out_of_province": out_of_province,
                # 「本轮的对标龙头」是清单自己给的产区关系判断，与「在不在浙江」
                # 是两件事：天堂伞在萧山、均胜在宁波 —— 不在那个产区，但在浙江。
                # 两个事实分开记，页面上才能同时说清（见 companies.py 的 region_source）。
                "benchmark": benchmark,
                "note": note,
                # 原始行留档。以前只留 website/phone/address 三个键，
                # 政府底表里的信用代码、成立日期、行业代码这些就全丢了 ——
                # 而那些正是这张表唯一有价值的东西。
                #
                # 但**不能整行照抄**：底表里有「法人姓名」「组织机构代码」这类列，
                # 即使来源已打码，把整行存下来仍然等于我们存了个人信息。
                # 所以按列白名单排除。
                "raw": {
                    "website": raw_site,
                    "phone": raw_phone,
                    "address": raw_address,
                    **{
                        name: value
                        for name, value in row.items()
                        if name and value and name not in PERSONAL_COLUMNS
                    },
                },
            }
        )

    if missing:
        raise ValueError(f"第 {missing[:10]} 行缺企业名称（共 {len(missing)} 行）")

    for record in records:
        tier = record["verify_tier"]
        if (
            tier
            and tier not in TIER_LETTERS
            and tier not in ("—", "-")
            and not any(keyword in tier for keyword, _ in TIER_DETAILS)
        ):
            unknown_tiers.add(tier)

    layers = build_layers(records, grade, tier_overrides)
    report = _report(
        records, rows_in, columns, missing, mobile_rejected, third_party,
        unknown_tiers, unknown_kinds, excluded, unresolved_towns, grade, source_file,
        unknown_areas, unknown_cluster_types,
    )
    return {"rows": records, "report": report, **layers}


def build_layers(records: list[dict], grade: str, tier_overrides: dict[str, str] | None = None) -> dict:
    """证据行 → 联系方式层 / 候选官网层（与既有 ``*_list.json`` / ``*_sites.json`` 同形）。"""
    layer_rows = []
    for record in records:
        row = {
            "name": record["name"],
            "source_label": tier_label(record["verify_tier"], grade, tier_overrides),
            # 联系方式那一层还要说清号码的**性质**（官网总机 / 第三方总机 / 待核）——
            # 「有电话」和「打得通」在镇街级清单里是两件事，A 档也不代表号码当前有效。
            "phone_nature": record.get("phone_nature", ""),
            "address_type": record.get("address_type", ""),
            "verify_tier": record["verify_tier"],
            "source_url": record["website"],
            "note": record["note"],
        }
        if record["website"]:
            row["website"] = record["website"]
            row["website_kind"] = record["website_kind"]
        if record["phone"]:
            row["public_phone"] = record["phone"]
        if record["email"]:
            row["public_email"] = record["email"]
        if record["address"]:
            row["address"] = record["address"]
        for field in ("founded_year", "registered_capital", "business_model", "address_registered"):
            if record.get(field):
                row[field] = record[field]
        layer_rows.append(row)

    # 候选官网层只装**候选**：清单给的域名 + 被判定为「不进官网字段」的裸域名。
    # 它们必须过验收门才会变成「站方核验 · 已比对官网」，所以放在这里安全 ——
    # 反过来，把集团/母公司站点塞进来就危险了：那条路径带不了 website_kind 标注，
    # 一旦验过就会被当成本企业自己的站发布出去。
    site_rows = [
        {"name": r["name"], "website": r["website"] or r.get("candidate_website", ""), "verify_tier": r["verify_tier"]}
        for r in records
        if r["website"] or r.get("candidate_website")
    ]
    return {"companies": layer_rows, "sites": site_rows}


def _report(
    records, rows_in, columns, missing, mobile_rejected, third_party,
    unknown_tiers, unknown_kinds, excluded, unresolved_towns, grade, source_file,
    unknown_areas: dict[str, int] | None = None,
    unknown_cluster_types: dict[str, int] | None = None,
) -> dict:
    def count(key):
        return sum(1 for r in records if r.get(key))

    tiers: dict[str, int] = {}
    for record in records:
        tiers[record["verify_tier"]] = tiers.get(record["verify_tier"], 0) + 1

    names = [r["name"] for r in records]
    duplicates = sorted({n for n in names if names.count(n) > 1})
    dispatch: dict[str, int] = {}
    for record in records:
        slug = record.get("list_cluster_slug") or "(未挂集群)"
        dispatch[slug] = dispatch.get(slug, 0) + 1
    clusters: dict[tuple, int] = {}
    for record in records:
        key = (
            record["cluster_type"],
            record["cluster_district"],
            record["track"],
            record["sub_track"],
        )
        clusters[key] = clusters.get(key, 0) + 1

    warnings: list[str] = []
    if excluded:
        warnings.append(
            f"{len(excluded)} 行因经营状态不是「在营」被排除："
            + "、".join(f"{e['name']}（{e['status']}）" for e in excluded[:5])
        )
    if unknown_tiers:
        warnings.append(
            "档位文案无法分类，已按「未复核」标注：" + "、".join(sorted(unknown_tiers))
        )
    if third_party:
        warnings.append(f"{len(third_party)} 行的链接是第三方商铺页，未计入官网字段")
    if unknown_kinds:
        warnings.append("官网类型未识别，链接未发布为官网：" + "、".join(sorted(unknown_kinds)))
    if unknown_areas:
        warnings.append(
            "产区列的值未识别，这些行没有集群归属："
            + "、".join(f"{name}（{count} 行）" for name, count in sorted(unknown_areas.items(), key=lambda kv: -kv[1]))
        )
    if unknown_cluster_types:
        warnings.append(
            "集群类型未识别，这些行没有集群归属："
            + "、".join(f"{name}（{count} 行）" for name, count in sorted(unknown_cluster_types.items(), key=lambda kv: -kv[1]))
        )
    if missing:
        warnings.append(f"{len(missing)} 行缺企业名，已跳过")

    return {
        "list_name": records[0]["list_name"] if records else "",
        "grade": grade,
        "source_file": source_file,
        "columns": [c for c in columns.values()],
        "source_columns": list(rows_in[0].keys()) if rows_in else [],
        "rows": len(records),
        "raw_rows": len(rows_in),
        "companies": len(set(names)),
        "duplicates": {n: names.count(n) for n in duplicates},
        "tiers": tiers,
        "batches": _counter(r.get("batch") for r in records),
        "with_website": count("website"),
        "with_phone": count("phone"),
        "with_email": count("email"),
        "with_address": count("address"),
        "out_of_province": sum(1 for r in records if r["out_of_province"]),
        "excluded": excluded,
        # 认不出来的街道：这些行的地区会留空。不猜，但要说出来 ——
        # 否则「有些企业没有地区」这件事没人会发现。
        "unresolved_towns": sorted(unresolved_towns),
        "mobile_rejected": mobile_rejected,
        "third_party": third_party,
        # 清单自带归属时的落位结果。挂不上的行会静默地不进任何集群页，
        # 所以导入报告必须把它列出来（与「集群（县市区）」的映射是两回事）。
        "cluster_dispatch": dict(sorted(dispatch.items(), key=lambda kv: -kv[1])),
        "cluster_unmatched": sorted({r["list_cluster_slug"] for r in records if not r.get("list_cluster_slug")}),
        # 「产区」列的实际落位：这份清单按产区分派，所以要能看出每个产区落到哪、
        # 哪些行没有归属（「其他产区」是作者自己写的「别处」，不是漏识别）。
        "production_areas": _counter(r.get("production_area") for r in records),
        "unknown_production_areas": dict(unknown_areas or {}),
        "production_area_dispatch": _area_dispatch(records),
        # 「集群类型」列（第四代产业群名单语境）的落位与未识别值。
        # 与产区分开报：一份清单可能两列都带，也可能只带其中一列。
        "cluster_type_dispatch": _cluster_type_dispatch(records),
        "unknown_cluster_types": dict(unknown_cluster_types or {}),
        "phone_nature": _counter(r.get("phone_nature") for r in records),
        "unknown_website_kinds": sorted(unknown_kinds),
        "annotated_names": sum(1 for r in records if r["match_name"] != r["name"]),
        "clusters": [
            {
                "cluster_type": key[0],
                "cluster_district": key[1],
                "track": key[2],
                "sub_track": key[3],
                "count": value,
            }
            for key, value in sorted(clusters.items(), key=lambda item: (-item[1], item[0]))
        ],
        "warnings": warnings,
    }


def _counter(values) -> dict[str, int]:
    out: dict[str, int] = {}
    for value in values:
        if value:
            out[str(value)] = out.get(str(value), 0) + 1
    return out


def _area_dispatch(records: list[dict]) -> dict[str, str]:
    """「产区」值 → 它落到的集群 slug（每行都列，落不上就是空串）。

    这份清单的归属**按产区分派**，一个产区几十行共用一个答案；
    只把产区与 slug 列出来，就不必对着几百行记录反推「这批到底挂哪去了」。
    """
    out: dict[str, str] = {}
    for record in records:
        area = record.get("production_area")
        if area:
            out[area] = record.get("list_cluster_slug", "")
    return dict(sorted(out.items(), key=lambda kv: kv[0]))


def _cluster_type_dispatch(records: list[dict]) -> dict[str, str]:
    """「集群类型」值 → 它落到的集群 slug（与 ``_area_dispatch`` 同一形状）。"""
    out: dict[str, str] = {}
    for record in records:
        kind = record.get("cluster_type")
        if kind:
            out[kind] = record.get("list_cluster_slug", "")
    return dict(sorted(out.items(), key=lambda kv: kv[0]))


# ---------------------------------------------------------------------------
# 注册表：哪些名单是通过接口导入的
# ---------------------------------------------------------------------------
def registry_path() -> Path:
    return settings.curated_dir / REGISTRY_FILE


def read_registry() -> dict:
    path = registry_path()
    if not path.exists():
        return {"_note": "通过 /api/admin/lists/import 导入的名单（同一 source_key 再导入即覆盖该名单）", "lists": []}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {"_note": "（注册表解析失败，已忽略）", "lists": []}


def imported_lists() -> list[dict]:
    """注册表里的名单条目（下游据此发现「有哪些名单」而不用改代码）。"""
    return [entry for entry in read_registry().get("lists", []) if entry.get("source_key")]


def write_atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False, suffix=".tmp")
    try:
        handle.write(text)
        handle.flush()
        os.fsync(handle.fileno())
    finally:
        handle.close()
    Path(handle.name).replace(path)


def output_paths(source_key: str, entry: dict | None = None) -> dict[str, Path]:
    """三份产物的路径。已有条目沿用它的文件名 —— 更新名单不该顺手改文件名。"""
    entry = entry or {}
    return {
        "source": settings.source_dir / entry.get("source_json", f"{source_key}.json"),
        "layer": settings.curated_dir / entry.get("layer_json", f"{source_key}_list.json"),
        "sites": settings.curated_dir / entry.get("sites_json", f"{source_key}_sites.json"),
    }


def import_list(
    *,
    text: str,
    list_name: str,
    grade: str,
    source_key: str,
    source_file: str = "",
    used_for: str = "",
    role: str = "",
    tier_overrides: dict[str, str] | None = None,
    default_cluster_slug: str = "",
    dry_run: bool = False,
) -> dict:
    """解析 + 落盘三份产物 + 更新注册表；``dry_run`` 只解析并回报告。

    ``default_cluster_slug``：整份清单**声明自己属于哪个集群**时用它
    （《现代纺织与服装企业调研清单》整份都是 415X「现代纺织与服装」赛道）。
    逐行自带的归属（「块状经济」列、行内「产区」列）**优先于**它 ——
    整份声明是兜底，不是覆盖。留空表示这份清单没有整份归属
    （如《全国无缝服装企业调研清单》按「产区」逐行分派）。
    """
    if grade not in GRADE_PREFIX:
        raise ValueError(f"未知核实程度 {grade!r}（可用：{'、'.join(GRADE_PREFIX)}）")
    if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{1,40}", source_key or ""):
        raise ValueError("source_key 只能是 2-41 位小写字母、数字、下划线或连字符")

    parsed = parse_list_csv(
        text,
        list_name=list_name,
        grade=grade,
        source_file=source_file,
        tier_overrides=tier_overrides,
        default_cluster_slug=default_cluster_slug,
        source_key=source_key,
    )
    registry = read_registry()
    entries = registry.setdefault("lists", [])
    existing = next((e for e in entries if e.get("source_key") == source_key), None)
    used_for = used_for.strip() or (existing or {}).get("used_for", "")
    # 「角色」是来源地图上那一列的一句话说明（「企业骨架」「产区企业层」…）。
    # 与 used_for 一样是覆盖式更新：再导入时不传就沿用旧值，不会被清空。
    role = role.strip() or (existing or {}).get("role", "")
    paths = output_paths(source_key, existing)
    report = parsed["report"]
    report["replaced"] = bool(existing)
    report["previous_rows"] = (existing or {}).get("rows")
    report["dry_run"] = dry_run

    if dry_run:
        report["outputs"] = {key: str(path) for key, path in paths.items()}
        report["next_step"] = "dry_run：未写入任何文件"
        return {"report": report, "parsed": parsed}

    write_atomic(paths["source"], json.dumps(parsed["rows"], ensure_ascii=False, indent=2) + "\n")
    write_atomic(paths["layer"], json.dumps(parsed["companies"], ensure_ascii=False, indent=2) + "\n")
    write_atomic(
        paths["sites"],
        json.dumps(
            {
                "_note": f"{list_name}里的官网，**未核实**，仅作候选（须通过本站验收门）。",
                "_source": source_file or list_name,
                "companies": parsed["sites"],
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
    )

    entry = {
        "source_key": source_key,
        "list_name": list_name,
        "grade": grade,
        "source_file": source_file,
        # 「这份名单在库里做什么」——来源声明直接用这段话，所以它跟着数据走，
        # 而不是写死在 routers/stats.py 里（写死了，名单一修订就变成假话）
        "used_for": used_for.strip(),
        "role": role,
        "imported_at": _now(),
        "rows": report["rows"],
        "companies": report["companies"],
        "tiers": report["tiers"],
        "with_website": report["with_website"],
        "with_phone": report["with_phone"],
        "with_address": report["with_address"],
        "out_of_province": report["out_of_province"],
        # 整份清单的归属声明（留空 = 逐行决定）。下游不需要据此挂集群
        # （每条记录已经带着 list_cluster_slug 了），它留在这里是为了让
        # 来源声明的读者看得懂「这几百家为什么都挂同一条赛道」。
        "default_cluster": default_cluster_slug,
        "source_columns": report["source_columns"],
        "source_json": paths["source"].name,
        "layer_json": paths["layer"].name,
        "sites_json": paths["sites"].name,
        "tier_labels": {
            tier: tier_label(tier, grade, tier_overrides) for tier in sorted(report["tiers"])
        },
    }
    entries = [e for e in entries if e.get("source_key") != source_key]
    entries.append(entry)
    entries.sort(key=lambda e: e["source_key"])
    registry["lists"] = entries
    registry["_note"] = "通过 /api/admin/lists/import 导入的名单（同一 source_key 再导入即覆盖该名单）"
    write_atomic(registry_path(), json.dumps(registry, ensure_ascii=False, indent=2) + "\n")

    report["outputs"] = {key: str(path) for key, path in paths.items()}
    report["entry"] = entry
    # 这一步决定了「导完要不要再做一件事」——直接给页面上那个按钮，
    # 而不是让人回终端敲命令（命令抄错一个字符就是一次失败的灌库，
    # 而且报错只有终端看得见）。
    report["next_step"] = (
        "产物已写入 data/，页面还看不到变化：点「按 data/ 清单重新灌库」后生效"
        "（终端等价命令：cd backend && ../.venv/bin/python -m app.cli seed --drop）"
    )
    return {"report": report, "parsed": parsed}
