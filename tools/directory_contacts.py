"""联系方式补全：从搜索结果摘要里取电话 / 邮箱 / 地址。

Why this layer exists: a large share of the collected 制造业 companies have no
website that can be proven to be theirs (see README「官网发现」). For those the
directory would otherwise show nothing at all, while the search snippet for the
company name very often already carries a switchboard number and an address,
because 顺企网 / 黄页88 / 天眼查 style aggregators publish exactly that.

This tool is **offline**: it only re-reads the result blocks that
``tools/search_discover.py`` already scraped and cached. That matters because
search requests — not fetches — are the scarce resource (see memory.md §3.9).
Re-running this tool is free, so the extractor can be improved and re-applied
without spending a single search query.

Guards (same "precision over coverage" stance as the site acceptance rule):

* the block must name the company (full name, or its brand core when the snippet
  truncates the name) — a result block that never mentions the company is not
  evidence about the company;
* numbers go through the project's single hardened validator (``normalize_phone``):
  valid 区号 allowlist, structural length, extension only with an explicit
  separator, and **mobile numbers are never kept**;
* boilerplate filter — a number that shows up for many different companies is the
  aggregator's own hotline, not the company's, and is dropped;
* the layer is written to its own file and labelled 第三方目录 · 未核验原站, so it
  can never be mistaken for a value taken from the company's own site.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from companies import load_companies  # noqa: E402
from discover_sites import core_name, now_iso, read_cache  # noqa: E402
from enrich_companies import clean_email, normalize_phone  # noqa: E402
from search_discover import BLOCK_CACHE, domain_of  # noqa: E402

OUT = ROOT / "data" / "curated" / "directory_contacts.json"

# 号码候选：先只做「像号码」的粗筛，是否成立完全交给 normalize_phone 判定。
# 分隔符只允许出现在数字之间，且两侧不能紧贴别的数字 —— 否则会把
# 一串 15 位数字（订单号、统一社会信用代码）截出前 12 位当成电话号码。
PHONE_CANDIDATE = re.compile(
    r"(?<![\d-])(?:0\d{2,3}(?:[-\s]?\d){7,8}|[48]00(?:[-\s]?\d){7})(?![\d-])"
)
MAIL_CANDIDATE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
ADDRESS_CANDIDATE = re.compile(r"地\s*址\s*[：:]\s*([^|｜\n]{6,60})")
# 地址至少要落在一个具体的行政区划或门牌上，否则「地址：浙江」这类残片也会被当成结果
ADDRESS_SHAPE = re.compile(r"[省市区县镇乡路街道号村]")
# 摘要里跟在地址后面的常见分隔/噪声。天眼查一类的摘要是「企业地址：… 经营范围：…」
# 这种连排格式，不截断就会把整段经营范围当成地址。
ADDRESS_TAIL = re.compile(
    r"\s{2,}|[（(]|经营范围|主营|电话|联系人|邮编|传真|邮箱|网址|手机|成立|注册资本|"
    r"统一社会|信用代码|法定代表人|登记状态|官网|行业|规模|简介|查看|更多|地图"
)


# 只有浙江区号（0570-0580）和全国热线才算数。
#
# 依据：这一层的企业都是浙江注册企业，总机几乎一定是浙江号码。摘要里冒出来的
# 外省号码更可能是聚合站自己所在地的号码被顺手带进了企业页，属于典型的
# 「看起来对、其实不是」——宁可留空。
ZHEJIANG_AREA_CODES = frozenset(f"057{n}" for n in range(10)) | {"0580"}


def is_local_line(phone: str) -> bool:
    """Keep 400/800 hotlines and Zhejiang landlines; drop anything else."""
    if phone.startswith(("400", "800")):
        return True
    return phone.split("-")[0] in ZHEJIANG_AREA_CODES


def _first_phone(block: str) -> str:
    for raw in PHONE_CANDIDATE.findall(block):
        value = normalize_phone(raw)
        if value and is_local_line(value):
            return value
    return ""

def _first_email(block: str) -> str:
    for raw in MAIL_CANDIDATE.findall(block):
        value = clean_email(raw)
        if value:
            return value
    return ""


def _first_address(block: str) -> str:
    match = ADDRESS_CANDIDATE.search(block)
    if not match:
        return ""
    candidate = ADDRESS_TAIL.split(match.group(1))[0].strip(" ，,。;；-")
    return candidate if ADDRESS_SHAPE.search(candidate) else ""


def extract_from_blocks(name: str, blocks: list[str]) -> dict:
    """Pull the first plausible phone / email / address out of a company's snippets.

    Each field remembers the block it came from, so ``source_url`` points at the
    page the value was read off — not at the company's own site, which may well
    appear in a different block.
    """
    brand = core_name(name)
    keys = {name, brand} if len(brand) >= 3 else {name}
    found = {"public_phone": "", "public_email": "", "address": ""}
    origins: dict[str, str] = {}

    for block in blocks:
        if not any(key in block for key in keys):
            continue
        host = domain_of(block)
        url = f"https://{host}/" if host else ""
        for field, extractor in (
            ("public_phone", _first_phone),
            ("public_email", _first_email),
            ("address", _first_address),
        ):
            if not found[field]:
                value = extractor(block)
                if value:
                    found[field] = value
                    origins[field] = url
        if all(found.values()):
            break

    primary = origins.get("public_phone") or origins.get("public_email") or origins.get("address") or ""
    return {**found, "source_url": primary}


def drop_boilerplate(rows: list[dict], max_share: int) -> int:
    """Remove numbers that appear for too many different companies.

    聚合站把自家客服电话放在每个页面上，这些号码会横跨大量互不相关的企业。
    一个号码被 N 家不同企业共用，几乎不可能是这 N 家的总机。
    """
    owners: dict[str, set[str]] = defaultdict(set)
    for row in rows:
        if row.get("public_phone"):
            owners[row["public_phone"]].add(row["name"])
    dropped = 0
    for row in rows:
        phone = row.get("public_phone")
        if phone and len(owners[phone]) >= max_share:
            row["public_phone"] = ""
            row["dropped_reason"] = f"该号码出现在 {len(owners[phone])} 家不同企业的摘要里，判定为聚合站公共号码"
            dropped += 1
    return dropped


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--max-share",
        type=int,
        default=3,
        help="同一号码出现在多少家不同企业的摘要里就丢弃（默认 3）",
    )
    parser.add_argument("--out", default=str(OUT))
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--only", action="append", default=[])
    args = parser.parse_args()

    companies = load_companies()
    if args.only:
        companies = [c for c in companies if any(k in c["name"] for k in args.only)]
    if args.limit:
        companies = companies[: args.limit]

    rows: list[dict] = []
    without_blocks = 0
    for company in companies:
        blocks = read_cache(BLOCK_CACHE, company["name"]) or []
        if not blocks:
            without_blocks += 1
            continue
        found = extract_from_blocks(company["name"], blocks)
        if not any(found[f] for f in ("public_phone", "public_email", "address")):
            continue
        rows.append({"name": company["name"], **found, "extracted_at": now_iso(), "layer": "search_directory"})

    dropped = drop_boilerplate(rows, args.max_share)
    rows = [r for r in rows if any(r.get(f) for f in ("public_phone", "public_email", "address"))]
    for row in rows:
        row["source_kind"] = "search_directory"

    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(
        json.dumps(sorted(rows, key=lambda r: r["name"]), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    print(f"companies={len(companies)} 有搜索摘要={len(companies) - without_blocks} 无摘要={without_blocks}")
    print(
        f"提取到联系方式的企业={len(rows)} "
        f"（电话 {sum(1 for r in rows if r['public_phone'])} / "
        f"邮箱 {sum(1 for r in rows if r['public_email'])} / "
        f"地址 {sum(1 for r in rows if r['address'])}），"
        f"因公共号码丢弃 {dropped}"
    )
    for row in rows[:15]:
        print(f"   {row['name'][:26]:28s} {row['public_phone']:14s} {row['address'][:30]}")


if __name__ == "__main__":
    main()
