"""Merge contact details for every company, with field-level provenance.

Priority (highest first):

1. ``data/curated/manual_sites.json``     — 人工补充（手工填写，字段留空即不覆盖）
2. ``data/curated/discovered.json``       — 站方逐个核验过的官网与联系方式
3. ``data/curated/enriched.json``         — 早期站方候选清单的核验结果（保留兼容）
4. ``data/curated/research_list.json``    — 第三方调研清单（武义户外运动与休闲），逐行带核实档位
   ``data/curated/seamless_list.json``    — 第三方调研清单（全国无缝服装，含省外产区）
   ``data/curated/cluster_reps_list.json`` — 第三方调研清单（第四批产业集群全国代表企业）
   以及 ``imported_lists.json`` 登记的任何名单（``/api/admin/lists/import`` 导入的）
5. ``data/curated/directory_contacts.json`` — 搜索摘要里的电话/地址，**原站未核验**

The layers are kept separate so a hand-filled value is recorded as such and never
silently presented as an officially-verified one. Every value carries a source
label, and mobile numbers are rejected outright — the directory does not publish
an individual's contact details.

This module lives in the backend package so the DB loader and the CLI
(``tools/contacts.py``) share exactly one implementation.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from ..config import settings
from .list_import import imported_lists

MANUAL_FILE = "manual_sites.json"
DISCOVERED_FILE = "discovered.json"
ENRICHED_FILE = "enriched.json"
DIRECTORY_FILE = "directory_contacts.json"
RESEARCH_FILE = "research_list.json"
SEAMLESS_FILE = "seamless_list.json"
REPS_FILE = "cluster_reps_list.json"

MOBILE_RE = re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)")
EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$")

LAYER_LABELS = {
    "manual": "人工补充 · 站方核对",
    # 这两层的标签以前写「自动发现 · 已抓取校验」/「站方候选 · 已抓取校验」。
    # 「抓取」描述的是**我们怎么做**，而不是这条数据有多可信 —— 读者不需要知道
    # 实现细节，只会把「已抓取」误读成「这家企业已被我们查过」。
    # 标签要回答的是「这个值是谁给的、核到什么程度」，所以改成核验口径的说法。
    "discovered": "站方核验 · 已比对官网",
    "enriched": "站方整理 · 已比对官网",
    # 这一层没有访问过企业自己的站点，只能从搜索结果摘要里读；标签必须让人一眼看出
    # 它和前两层不是一个可信度，所以不写「已核验」。
    "directory": "搜索结果摘要 · 原站未核验",
    # research_list 的标签**逐行不同**（A 官网公示 / B 工商可核 / C 未核线索），
    # 行内 source_label 优先；这里是缺省值。
    "research_list": "第三方调研 · 未复核",
    "seamless_list": "第三方调研 · 未复核",
    "reps_file": "第三方调研 · 未复核",
    # /api/admin/lists/import 导入的名单：层标签只是兜底，行内 source_label 才是准的
    "imported_list": "外部名单 · 未复核",
}

# 输出字段 → 来源标签字段
FIELD_SOURCES = {
    "website": "website",
    "public_phone": "phone",
    "public_email": "email",
    "address": "address",
}


def _load_json(path: Path, default):
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return default


def clean_phone(value: str) -> str:
    value = (value or "").strip()
    if not value or MOBILE_RE.search(value):
        return ""
    return value


# 邮箱本地部分里的手机号（``13817608043@139.com``）。邮箱地址本身是公开的，
# 但本地部分就是那个人手机号 —— 展示它等于把个人号码印在页面上，
# 与「手机号一律不采集、不展示」这条承诺直接冲突，而且这是绕过承诺最容易的路径：
# 承诺里写的是「手机号」，没人会想到号码藏在邮箱里。
MAILBOX_MOBILE_RE = re.compile(r"^(?:(?:\+?86)?1[3-9]\d{9}|1[3-9][\s._-]?\d{4}[\s._-]?\d{4})$")


def clean_email(value: str) -> str:
    value = (value or "").strip().lower()
    if not EMAIL_RE.match(value):
        return ""
    local = value.split("@", 1)[0]
    digits = re.sub(r"[\s._-]", "", local)
    if MAILBOX_MOBILE_RE.match(local) or (digits.isdigit() and len(digits) == 11):
        return ""
    return value


def load_contacts() -> dict[str, dict]:
    """name → merged contact record (only companies with at least one value kept)."""
    merged: dict[str, dict] = {}

    def apply(row: dict, layer: str) -> None:
        name = (row.get("name") or "").strip()
        if not name:
            return
        record = merged.setdefault(name, {"name": name, "sources": {}, "layers": []})
        if layer not in record["layers"]:
            record["layers"].append(layer)

        # 逐行标签优先：同一层里不同行的核实程度可能差很远（第三方调研清单的
        # A 档是官网公示、C 档只是第三方目录线索），不能用层标签一笔带过。
        label = row.get("source_label") or LAYER_LABELS.get(layer, layer)
        contributed = False
        for field, label_key in FIELD_SOURCES.items():
            incoming = row.get(field, "")
            if field == "public_phone":
                incoming = clean_phone(incoming)
            elif field == "public_email":
                incoming = clean_email(incoming)
            if not incoming or record.get(field):
                continue  # 已有更高优先级来源
            record[field] = incoming
            record["sources"][label_key] = label
            contributed = True
            record[f"{label_key}_url"] = row.get("source_url", "")

        # 企业属性（成立年份 / 注册资本）只认**人工补充**这一层。
        #
        # 其它层只是把名单里的原值原样带出来 —— 站方核验层的记录本来就是
        # `dict(company)`，也带着 founded_year。若按「任意层都可提供」处理，
        # 名单里抄来的年份会被标成「站方核验 · 已比对官网」，
        # 等于凭空给一个从没核过的字段盖了「已核验」的章。
        if layer == "manual":
            for field in ("founded_year", "registered_capital"):
                incoming = row.get(field)
                if incoming in (None, "") or record.get(field) not in (None, ""):
                    continue
                record[field] = incoming
                record["sources"][field] = label

        # 「来源链接 / 核验日期」只能由**真的贡献了字段**的那一层带入。
        #
        # 自动发现层就算一家都没抓到，也会写下 source_title/verified_at 表示「这次跑过了」。
        # 若不加这个条件，那些字段会盖到别层的数据上：调研清单里的电话会被标上
        # 今天的「核验日期」，看起来像是我们核验过 —— 而实际上一眼都没看过。
        # `website_kind` 是「官网」这个字段的属性，跟着官网一起走
        if contributed and row.get("website_kind") and not record.get("website_kind"):
            record["website_kind"] = row["website_kind"]
        # `phone_nature` 同理，而且更要紧：镇街级清单里「有官网」与「打得通电话」
        # 是两件事，A 档只代表官网公示、调研方未做回拨核验 ——
        # 不给号码标性质，读者会把「官网上印着」读成「这个号码是通的」。
        if contributed and row.get("phone_nature") and not record.get("phone_nature"):
            record["phone_nature"] = row["phone_nature"]
        if contributed and row.get("address_type") and not record.get("address_type"):
            record["address_type"] = row["address_type"]

        if contributed:
            if row.get("source_url") and not record.get("source_url"):
                record["source_url"] = row["source_url"]
            if row.get("verified_at") and not record.get("verified_at"):
                record["verified_at"] = row["verified_at"]
        if row.get("note") and layer == "manual":
            record["manual_note"] = row["note"]
        if row.get("enrich_status") and not record.get("enrich_status"):
            record["enrich_status"] = row["enrich_status"]

    for row in _load_json(settings.curated_dir / MANUAL_FILE, {"companies": []}).get("companies", []):
        apply(row, "manual")

    # 内置层（随代码维护）
    builtin = (
        ("discovered", DISCOVERED_FILE),
        ("enriched", ENRICHED_FILE),
        ("research_list", RESEARCH_FILE),
        ("seamless_list", SEAMLESS_FILE),
        ("reps_file", REPS_FILE),
    )
    seen = {filename for _, filename in builtin}
    # 通过接口导入的名单：注册表说有哪些层就有哪些层，不必再改代码。
    # 已在内置层里的文件名跳过 —— 同一个文件只应用一次，否则同一行会被记两次来源。
    imported = [
        ("imported_list", entry["layer_json"])
        for entry in imported_lists()
        if entry.get("layer_json") and entry["layer_json"] not in seen
    ]
    for layer, filename in (*builtin, *imported, ("directory", DIRECTORY_FILE)):
        for row in _load_json(settings.curated_dir / filename, []):
            apply(row, layer)

    for record in merged.values():
        has_site = bool(record.get("website"))
        # 地址也算联系方式：对一家工厂来说「有地址」就是可用的商家信息，
        # 早先漏算它会让「待补清单」把已经有地址的企业也列进去。
        has_contact = bool(
            record.get("public_phone") or record.get("public_email") or record.get("address")
        )
        if has_site and has_contact:
            record["contact_status"] = "full"
        elif has_site or has_contact:
            record["contact_status"] = "partial"
        else:
            record["contact_status"] = "empty"
    return merged


def validate_manual(known_names: set[str]) -> list[str]:
    """Report manual entries that would not take effect."""
    problems: list[str] = []
    payload = _load_json(settings.curated_dir / MANUAL_FILE, {"companies": []})

    for index, row in enumerate(payload.get("companies", []), 1):
        name = (row.get("name") or "").strip()
        if not name:
            problems.append(f"#{index}: 缺少 name")
            continue
        if name not in known_names:
            problems.append(f"#{index}: 「{name}」不在官方名单中（不会生效）")
        if row.get("public_phone") and MOBILE_RE.search(row["public_phone"]):
            problems.append(f"#{index}: 「{name}」的电话是手机号，已按边界规则拒绝")
        if row.get("public_email") and not EMAIL_RE.match(str(row["public_email"]).strip().lower()):
            problems.append(f"#{index}: 「{name}」的邮箱格式不合法")
        if row.get("website") and not str(row["website"]).startswith(("http://", "https://")):
            problems.append(f"#{index}: 「{name}」的 website 需以 http(s):// 开头")
    return problems