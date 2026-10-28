"""武义县户外运动与休闲企业清单 → JSON（第三方调研名单，第三阶段导入）。

来源：``data/source/wuyi_outdoor_2026.csv``（调研方交付的原始文件，逐字保存）。
这份名单**不是官方名单**，是第三方调研清单，因此：

* 页面标 `站方整理`（`site_curated`），不与两个官方名单同款式呈现；
* 逐行保留调研方的**核实档位**（A 官网公示 / B 工商可核 / C 线索未核），
  因为它决定了这一行的联系方式能不能直接用；
* 名单里的「官网」「电话」列含有大量**哨兵值**（`未获取` / `无官网（…）` /
  `官网为占位符`）——这些**不是数据**，必须在这里就丢掉，
  否则会以「有官网」的形态进入库，这是最难被发现的污染。

解析结果的字段名与另外两个名单文件对齐（`name` / `list_name` / `product` 等），
这样骨架层（`seed/companies.py`、`tools/companies.py`）可以用同一套代码消费它。
"""

from __future__ import annotations

import csv
import io
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CSV_PATH = ROOT / "data" / "source" / "wuyi_outdoor_2026.csv"
OUT_PATH = ROOT / "data" / "source" / "wuyi_outdoor_2026.json"
# 派生产物：联系方式层 + 候补官网清单（都进 data/curated/，后续由发现管线消费）
LAYER_PATH = ROOT / "data" / "curated" / "research_list.json"
SEED_PATH = ROOT / "data" / "curated" / "research_sites.json"

# 逐档联系方式标签。
#
# 这三档的差距是**实质性的**，不能共用一个「站方整理」标签：
# A 档的信息印在企业官网的联系页上，B 档是企业工商可核但联系方式来自第三方目录，
# C 档连来源都只有第三方目录。用同一个标签就等于把 C 档线索说成 A 档公示。
TIER_LABELS = {
    "A 官网公示": "第三方调研 · 官网公示（未复核）",
    "B 工商可核": "第三方调研 · 工商可核（联系方式来自第三方）",
    "C 线索（未核）": "第三方调研 · 未核线索",
    "C 第三方目录·存疑": "第三方调研 · 来源存疑（仅线索）",
}

LIST_NAME = "武义县户外运动与休闲企业清单（第三方调研）"
CITY = "金华市"
DISTRICT = "武义县"

# 官网列的哨兵：任何一条都表示「调研方没有拿到可用官网」
WEBSITE_SENTINELS = ("未获取", "无官网", "占位符")
# 电话列的哨兵：注意「官网为占位符」——它说明官网电话栏是 0000-00000000
PHONE_SENTINELS = ("未获取", "占位符", "无")
ADDRESS_SENTINELS = ("未获取", "未公示")
# 地址要落到具体门牌/园区，否则「武义县」这种残片没有检索价值
ADDRESS_SHAPE = re.compile(r"[街道镇乡路号村区园]")
PHONE_SHAPE = re.compile(r"^(?:0\d{2,3}-?\d{7,8}(?:-\d{1,6})?|400-?\d{3}-?\d{4})$")

MOBILE_REJECT = "手机号（本站不采集个人联系方式）"

TIERS = {
    "A 官网公示": "官网联系页 / 页脚公示的信息",
    "B 工商可核": "工商登记可核，联系方式来自第三方目录（未核）",
    "C 线索（未核）": "第三方目录线索，未经任何核实",
    "C 第三方目录·存疑": "第三方目录来源自相矛盾，仅作线索",
}


def clean_website(raw: str) -> str:
    value = (raw or "").strip()
    if not value or any(s in value for s in WEBSITE_SENTINELS):
        return ""
    if not value.startswith(("http://", "https://")):
        return ""
    return value.rstrip("/")


def clean_phone(raw: str) -> tuple[str, str]:
    """Return ``(display, rejected_reason)``.

    电话列里混了三种东西：真座机 / 多个号码用「/」并排 / **个人手机号**。
    最后一类必须拒绝——这份调研名单是「A 官网公示」，但官网公示的也可能只是
    老板的手机号，而本站不采集个人联系方式（与全站边界一致）。
    """
    value = re.sub(r"\s+", "", (raw or "").strip())
    if not value or any(s in value for s in PHONE_SENTINELS):
        return "", ""
    for candidate in re.split(r"[/／、,，;；]+", value):
        digits = re.sub(r"\D", "", candidate)
        if re.fullmatch(r"1[3-9]\d{9}", digits):
            return "", "手机号（本站不采集个人联系方式）"
        if PHONE_SHAPE.match(candidate):
            return candidate.replace("－", "-"), ""
    return "", "号码形态不合法"


def clean_address(raw: str) -> str:
    value = re.sub(r"\s+", "", (raw or "").strip())
    if not value or any(s in value for s in ADDRESS_SENTINELS):
        return ""
    if len(value) < 8 or not ADDRESS_SHAPE.search(value):
        return ""
    return value


def write_derived(records: list[dict]) -> None:
    """写出两个派生文件，供发现管线与联系方式合并消费。

    * ``research_sites.json`` —— 候补官网清单，喂给 ``discover_sites.py``。
      调研方说某家企业的官网是这个，**只算候选**：必须过和别的候选同一道验收门
      （页面得自己证明归属）。过了的会以「自动发现 · 已抓取校验」进库，
      比这一层的「第三方调研 · 未复核」可信度更高。
    * ``research_list.json`` —— 联系方式层，优先级排在自动发现之后。
      每一行带 ``source_label``，按核实档位降级标注。
    """
    seed_rows = [
        {"name": r["name"], "website": r["website"], "verify_tier": r["verify_tier"]}
        for r in records
        if r["website"]
    ]
    SEED_PATH.write_text(
        json.dumps(
            {
                "_note": "武义县户外运动与休闲企业清单里调研方给出的官网，**未核实**，仅作候选；"
                         "须通过 discover_sites.py 的验收门才会以「站方核验 · 已比对官网」入库。",
                "_source": "data/source/wuyi_outdoor_2026.csv",
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
        if r["website"]:
            row["website"] = r["website"]
        if r["phone"]:
            row["public_phone"] = r["phone"]
        if r["email"]:
            row["public_email"] = r["email"]
        if r["address"]:
            row["address"] = r["address"]
        if any(row.get(k) for k in ("website", "public_phone", "public_email", "address")):
            layer_rows.append(row)

    LAYER_PATH.write_text(
        json.dumps(sorted(layer_rows, key=lambda r: r["name"]), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"  候补官网清单 {len(seed_rows)} 条 → {SEED_PATH.relative_to(ROOT)}")
    print(f"  联系方式层 {len(layer_rows)} 条 → {LAYER_PATH.relative_to(ROOT)}")


def main() -> None:
    raw = CSV_PATH.read_text(encoding="utf-8-sig")
    rows = list(csv.DictReader(io.StringIO(raw)))

    assert rows, "CSV 为空"
    assert list(rows[0].keys())[2] == "公司名称", f"列顺序异常: {list(rows[0].keys())}"

    records = []
    dropped_website = dropped_phone = dropped_address = 0
    mobile_rejected: list[str] = []
    malformed: list[tuple[str, str]] = []
    for row in rows:
        name = (row["公司名称"] or "").strip()
        assert name, "存在空企业名"
        tier = (row["核实等级"] or "").strip()
        assert tier in TIERS, f"{name}: 未知核实档位 {tier!r}"

        website = clean_website(row["官网"])
        phone, phone_reject = clean_phone(row["电话"])
        address = clean_address(row["地址"])
        dropped_website += bool((row["官网"] or "").strip()) and not website
        dropped_phone += bool((row["电话"] or "").strip()) and not phone
        dropped_address += bool((row["地址"] or "").strip()) and not address
        if phone_reject == MOBILE_REJECT:
            mobile_rejected.append(name)
        elif phone_reject:
            malformed.append((name, (row["电话"] or "").strip()))

        note = (row["来源与备注"] or "").strip()
        if phone_reject:
            # 把「为什么这里没有电话」写进备注，页面才不会看起来像漏填
            note = f"{note}｜电话未收录：{phone_reject}".strip("｜")

        founded = (row["成立年份"] or "").strip()
        records.append(
            {
                "list_name": LIST_NAME,
                "seq": int(row["序号"]),
                "name": name,
                "product": (row["主营产品"] or "").strip().replace("／", "/"),
                "city": CITY,
                "district": DISTRICT,
                "region_raw": "武义县（名单所属产区）",
                "verify_tier": tier,
                "tier_note": TIERS[tier],
                "website": website,
                "phone": phone,
                "email": (row["邮箱"] or "").strip().lower(),
                "address": address,
                "founded_year": int(founded) if founded.isdigit() else None,
                "registered_capital": (row["注册资本"] or "").strip(),
                "note": note,
                # 原始值留档：出问题时能证明我们丢的是哨兵而不是数据
                "raw": {
                    "website": (row["官网"] or "").strip(),
                    "phone": (row["电话"] or "").strip(),
                    "address": (row["地址"] or "").strip(),
                },
            }
        )

    seqs = [r["seq"] for r in records]
    assert seqs == list(range(1, len(records) + 1)), f"序号不连续: {seqs[:5]}…"
    assert len(records) == 65, f"行数应为 65，实际 {len(records)}"
    assert all(r["product"] for r in records), "存在无主营产品的行"

    tiers: dict[str, int] = {}
    for record in records:
        key = record["verify_tier"][0]
        tiers[key] = tiers.get(key, 0) + 1
    assert tiers == {"A": 7, "B": 10, "C": 48}, f"档位分布异常: {tiers}"

    # 哨兵必须被丢掉，且数量要能对上：留下多少条真实值由断言固定住，
    # 以后换一份名单、哨兵写法变了，这里会立刻失败而不是静默放行。
    with_website = sum(1 for r in records if r["website"])
    with_phone = sum(1 for r in records if r["phone"])
    assert with_website == 9, f"可用官网应为 9 条，实际 {with_website}"
    assert with_phone == 6, f"可用电话应为 6 条，实际 {with_phone}"
    # 名单里有 2 家「A 官网公示」的电话栏其实是老板的手机号，必须被拒绝。
    # 这条断言就是为了防止以后有人「顺手」把它放开。
    assert len(mobile_rejected) == 2, f"应拒绝 2 个手机号，实际 {mobile_rejected}"
    assert not malformed, f"存在无法解析的号码: {malformed}"
    assert all(not any(s in r["website"] for s in WEBSITE_SENTINELS) for r in records)
    assert all(not any(s in r["phone"] for s in PHONE_SENTINELS) for r in records)

    OUT_PATH.write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"{len(records)} 行 → {OUT_PATH.relative_to(ROOT)}")
    print(f"  档位: {tiers}")
    print(f"  真实官网 {with_website}（丢弃哨兵 {dropped_website}）")
    print(f"  真实电话 {with_phone}（丢弃哨兵 {dropped_phone}，拒绝手机号 {len(mobile_rejected)}：{'、'.join(mobile_rejected)}）")
    print(f"  可用地址 {sum(1 for r in records if r['address'])}（丢弃过粗/哨兵 {dropped_address}）")
    print(f"  成立年份 {sum(1 for r in records if r['founded_year'])} / 注册资本 {sum(1 for r in records if r['registered_capital'])}")
    write_derived(records)


if __name__ == "__main__":
    main()
