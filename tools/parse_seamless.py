"""全国无缝服装企业清单 → JSON（第三方调研清单，第三阶段导入）。

来源：``docs/全国无缝服装企业清单.csv``（同时留档到 ``data/source/``），
调研方法论见 ``docs/全国无缝服装企业调研清单.html``。

这份清单和《武义县户外运动与休闲企业清单》是同一位调研方的同一套分档规则，
但有**两处品类专属规则**，必须实现，否则数据会变形：

**规则一 · 地址拆成两个字段。** 两家企业（知音纺织、伊秀服饰）在自己的官网上
给出两个不同地址。这不是抓错，而是企业实际有注册地、办公地、厂址多处。
只存一个字符串，无论存哪个，后续都会被质疑。所以：

* ``address`` —— 对外展示的那个（优先官网公示的办公地/厂址）
* ``address_registered`` —— 工商注册地（**只在与展示地址不同时**才填）
* 两处冲突的解释写进 ``note``，不塞进地址字段

**规则二 · 手机号按「性质」判断。** 调研方建议：官网公示的招聘专线/客服热线可以保留
并标注性质，具名的个人手机号剔除。

**本站不采纳这条建议**：本库对外的边界声明是「手机号一律不采集、不展示」，
这是一条已公开的承诺，不能为了让 A 档多两条电话而悄悄放宽——
而且「招聘专线」和「个人手机」在页面上很难稳定区分（天派针织那条既是招聘专线、
也是一个 173 段的个人号段）。所以手机号一律不收录，但**把它的存在与性质写进备注**，
页面才不会看起来像漏填：

    电话未收录：官网仅公示招聘专线（手机号形态，+86 173…），按本站边界不采集手机号

**C 档也对外展示（站方决定）。** 两份调研清单的「可用范围」一栏都写着 C 档
「不可对外展示；仅作内部待核清单」。本站第一版照此实现了 `publishable` 过滤，
后来站方决定**取消这条限制**：C 档线索照常展示，靠逐字段的
「第三方调研 · 未核线索」标签来交代可信度，而不是靠隐藏。
所以解析器不再输出任何可见性字段 —— 可见性一旦回到数据里，
就会出现「同一批线索，有的能搜到有的搜不到」这种没人能解释的状态。
"""

from __future__ import annotations

import csv
import io
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CSV_PATH = ROOT / "data" / "source" / "seamless_garment_2026.csv"
OUT_PATH = ROOT / "data" / "source" / "seamless_garment_2026.json"
LAYER_PATH = ROOT / "data" / "curated" / "seamless_list.json"
SEED_PATH = ROOT / "data" / "curated" / "seamless_sites.json"

LIST_NAME = "全国无缝服装企业清单（第三方调研）"
EXPECTED_ROWS = 19

# 产区 → 展示用的行政区划 + 集群 slug + 是否省外
#
# 省外产区**单独建档**（调研方案 §01/§07 第六步：盐步与晋江不与义乌混表），
# 因为代工型（义乌、谷饶）与品牌型（盐步）的字段需求不同，混表会让字段互相打架。
PRODUCTION_AREAS = {
    "浙江义乌": {
        "cluster": "yiwu-seamless",
        "city": "金华市",
        "district": "义乌市",
        "out_of_province": False,
        "business_model": "代工型（OEM/ODM 为主）",
    },
    "广东汕头": {
        "cluster": "shantou-gurao-seamless",
        "city": "汕头市",
        "district": "潮阳区",
        "out_of_province": True,
        "business_model": "代工型（OEM/ODM 为主）",
    },
    "广东佛山": {
        "cluster": "foshan-yanbu-brands",
        "city": "佛山市",
        "district": "南海区",
        "out_of_province": True,
        "business_model": "品牌型（面向国内消费者）",
    },
    "福建晋江": {
        "cluster": "jinjiang-shenhu-underwear",
        "city": "泉州市",
        "district": "晋江市",
        "out_of_province": True,
        "business_model": "品类专精型（男士内裤）",
    },
}

WEB_SENTINELS = ("未获取", "无官网", "占位符")
PHONE_SENTINELS = ("未获取", "占位符", "官网未公示")
MAIL_SENTINELS = ("未获取", "未完整获取", "官网未公示", "公示")
ADDRESS_SENTINELS = ("未获取", "未公示", "待补", "具体地址")
PHONE_SHAPE = re.compile(r"^(?:0\d{2,3}-?\d{7,8}(?:-\d{1,6})?|400-?\d{3}-?\d{4}|800-?\d{3}-?\d{4})$")
MOBILE_SHAPE = re.compile(r"^(?:\+?86)?1[3-9]\d{9}$")
# 地址要落到具体门牌/园区；「义乌市」这种只剩行政区的值没有检索价值
ADDRESS_SHAPE = re.compile(r"[路街镇乡号村区园大厦栋幢区]")

TIER_LABELS = {
    "A 官网公示": "第三方调研 · 官网公示（未复核）",
    "B 官网可核·电话待补": "第三方调研 · 官网可核（电话待补）",
    "B 工商可核": "第三方调研 · 工商可核（联系方式来自第三方）",
    "C 线索（未核）": "第三方调研 · 未核线索",
}


def strip_qualifier(raw: str) -> str:
    """去掉括号里的说明（「0754-83356789（无缝部门）」→ 号码本身）。"""
    return re.sub(r"[（(][^）)]*[）)]", "", raw).strip()


def clean_phone(raw: str) -> tuple[str, str]:
    """Return ``(display, rejected_reason)`` —— 与 parse_outdoor 同一套判断。"""
    value = re.sub(r"\s+", "", (raw or "").strip())
    if not value or any(s in value for s in PHONE_SENTINELS):
        return "", ""
    candidates = [strip_qualifier(part) for part in re.split(r"[/／、,，;；]+", value)]
    for candidate in candidates:
        digits = re.sub(r"\D", "", candidate)
        if MOBILE_SHAPE.fullmatch(re.sub(r"\s", "", value)):
            return "", "官网仅公示手机号"
        if PHONE_SHAPE.match(candidate):
            return candidate.replace("－", "-"), ""
    if MOBILE_SHAPE.fullmatch(re.sub(r"\s", "", value)):
        return "", "官网仅公示手机号"
    if "手机" in raw:
        return "", "官网仅公示手机号"
    return "", "号码形态不合法"


def clean_website(raw: str) -> str:
    value = (raw or "").strip()
    if not value or any(s in value for s in WEB_SENTINELS):
        return ""
    return value.rstrip("/") if value.startswith(("http://", "https://")) else ""


def clean_mail(raw: str) -> str:
    value = (raw or "").strip().lower()
    if not value or any(s in value for s in MAIL_SENTINELS):
        return ""
    return value if re.fullmatch(r"[a-z0-9._%+-]+@[a-z0-9.-]+\.[a-z]{2,}", value) else ""


def split_address(raw: str) -> tuple[str, str, str]:
    """Return ``(展示地址, 注册地, 地址口径说明)``.

    三类输入：
    1. 单地址 → 原样作展示地址；
    2. 「官网两处地址不一致：A（官网首页）／B（搜索摘要）」→ 官网那个作展示地址，另一处写进说明；
    3. 「A（官网）／B（工商）」→ 官网作展示地址，工商那个进 ``address_registered``。
    """
    value = re.sub(r"\s+", "", (raw or "").strip())
    if not value or any(s in value for s in ADDRESS_SENTINELS):
        return "", "", ""
    if "不一致" not in value and "／" not in value:
        return (value if ADDRESS_SHAPE.search(value) and len(value) >= 8 else ""), "", ""

    body = value.split("：", 1)[1] if "：" in value else value
    parts = [p.strip() for p in re.split(r"／|/", body) if p.strip()]
    note = f"调研清单记录的地址不止一处：{value}"
    display = registered = ""
    for part in parts:
        bare = re.sub(r"[（(][^）)]*[）)]", "", part).strip()
        if "工商" in part or "注册" in part:
            if ADDRESS_SHAPE.search(bare):
                registered = bare
            continue
        if not display and ADDRESS_SHAPE.search(bare) and len(bare) >= 8:
            display = bare
    return display, registered, note


def main() -> None:
    rows = list(csv.DictReader(io.StringIO(CSV_PATH.read_text(encoding="utf-8-sig"))))
    assert rows, "CSV 为空"
    assert len(rows) == EXPECTED_ROWS, f"行数应为 {EXPECTED_ROWS}，实际 {len(rows)}"

    records = []
    mobile_rejected: list[str] = []
    conflict_addresses: list[str] = []

    for row in rows:
        name = (row["公司名称"] or "").strip()
        assert name, "存在空企业名"
        tier = (row["核实等级"] or "").strip()
        assert tier in TIER_LABELS, f"{name}: 未知核实档位 {tier!r}"
        area_key = (row["产区"] or "").strip()
        assert area_key in PRODUCTION_AREAS, f"{name}: 未知产区 {area_key!r}"
        area = PRODUCTION_AREAS[area_key]

        website = clean_website(row["官网"])
        phone, phone_reject = clean_phone(row["电话"])
        if phone_reject == "官网仅公示手机号":
            mobile_rejected.append(name)
        address, registered, address_note = split_address(row["地址"])
        if address_note:
            conflict_addresses.append(name)

        note = (row["来源与备注"] or "").strip()
        if phone_reject:
            raw_phone = (row["电话"] or "").strip()
            note = f"{note}｜电话未收录：{phone_reject}（原文「{raw_phone}」），按本站边界不采集手机号"
        if address_note:
            note = f"{note}｜{address_note}"

        founded = (row["成立年份"] or "").strip()
        records.append(
            {
                "list_name": LIST_NAME,
                "seq": int(row["序号"]),
                "name": name,
                "product": (row["主营产品"] or "").strip(),
                "production_area": area_key,
                "verify_tier": tier,
                "city": area["city"],
                "district": area["district"],
                "region_raw": area_key,
                "out_of_province": area["out_of_province"],
                "business_model": area["business_model"],
                "website": website,
                "phone": phone,
                "email": clean_mail(row["邮箱"]),
                "address": address,
                "address_registered": registered,
                "fax": strip_qualifier(row["传真"]) if re.match(r"^0\d{2,3}-?\d{7,8}$", strip_qualifier(row["传真"])) else "",
                "founded_year": int(founded) if founded.isdigit() else None,
                "registered_capital": "",
                "note": note,
                "raw": {
                    "website": (row["官网"] or "").strip(),
                    "phone": (row["电话"] or "").strip(),
                    "address": (row["地址"] or "").strip(),
                },
            }
        )

    seqs = [r["seq"] for r in records]
    assert seqs == list(range(1, len(records) + 1)), f"序号不连续: {seqs}"
    assert all(r["product"] for r in records), "存在无主营产品的行"

    tiers: dict[str, int] = {}
    for record in records:
        key = record["verify_tier"][0]
        tiers[key] = tiers.get(key, 0) + 1
    assert tiers == {"A": 4, "B": 6, "C": 9}, f"档位分布异常: {tiers}"

    # 哨兵与手机号的处理结果固定住：换一份清单、写法变了，这里会立刻失败
    with_site = sum(1 for r in records if r["website"])
    with_phone = sum(1 for r in records if r["phone"])
    assert with_site == 9, f"可用官网应为 9 条，实际 {with_site}"
    assert with_phone == 3, f"可用业务座机应为 3 条，实际 {with_phone}"
    assert len(mobile_rejected) == 2, f"应拒绝 2 个手机号，实际 {mobile_rejected}"
    assert conflict_addresses == [
        "浙江知音纺织科技有限公司",
        "义乌市伊秀服饰有限公司",
        "浙江芊叶服饰有限公司",
    ], conflict_addresses
    assert any(r["address_registered"] for r in records), "地址拆分没有生效（注册地字段为空）"
    assert all(not any(s in r["website"] for s in WEB_SENTINELS) for r in records)
    assert all(not any(s in r["phone"] for s in PHONE_SENTINELS) for r in records)

    OUT_PATH.write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")
    write_derived(records)

    print(f"{len(records)} 行 → {OUT_PATH.relative_to(ROOT)}")
    print(f"  档位: {tiers}")
    print(f"  官网 {with_site} / 业务座机 {with_phone} / 邮箱 {sum(1 for r in records if r['email'])}")
    print(f"  手机号未收录 {len(mobile_rejected)}：{'、'.join(mobile_rejected)}")
    print(f"  地址两处不一致 {len(conflict_addresses)}：{'、'.join(conflict_addresses)}")
    print(f"  省外产区 {sum(1 for r in records if r['out_of_province'])} 家")


def write_derived(records: list[dict]) -> None:
    """候补官网清单 + 联系方式层（标签按档位逐行降级）。"""
    seed_rows = [
        {"name": r["name"], "website": r["website"], "verify_tier": r["verify_tier"]}
        for r in records
        if r["website"]
    ]
    SEED_PATH.write_text(
        json.dumps(
            {
                "_note": "全国无缝服装企业清单里调研方给出的官网，**未核实**，仅作候选；"
                         "须通过 discover_sites.py 的验收门才会以「站方核验 · 已比对官网」入库。",
                "_source": "docs/全国无缝服装企业清单.csv",
                "companies": seed_rows,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    layer_rows = []
    for r in records:
        row = {
            "name": r["name"],
            "source_label": TIER_LABELS[r["verify_tier"]],
            "verify_tier": r["verify_tier"],
            "source_url": r["website"],
            "note": r["note"],
        }
        for field in ("website", "phone", "email", "address"):
            key = {"website": "website", "phone": "public_phone", "email": "public_email", "address": "address"}[field]
            if r[field]:
                row[key] = r[field]
        if any(row.get(k) for k in ("website", "public_phone", "public_email", "address")):
            layer_rows.append(row)

    LAYER_PATH.write_text(
        json.dumps(sorted(layer_rows, key=lambda r: r["name"]), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"  候补官网 {len(seed_rows)} 条 → {SEED_PATH.relative_to(ROOT)}")
    print(f"  联系方式层 {len(layer_rows)} 条 → {LAYER_PATH.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
