"""CSV round-trip for the manual contact layer (Excel-friendly).

Workflow:

1. 导出待补充清单：``python tools/manual_csv.py export``
   → ``data/curated/manual_sites_template.csv``，一行一家缺联系方式的企业。
2. 用 Excel / Numbers / 任意编辑器填 ``website`` / ``public_phone`` / ``public_email`` /
   ``address`` / ``source_url`` / ``note`` 列。留空的行会被忽略。
3. 导入：``python tools/manual_csv.py import``
   → 逐行校验后写回 ``data/curated/manual_sites.json``（已存在的企业按字段合并）。
4. 重新灌库：``cd backend && ../.venv/bin/python -m app.cli seed``

校验规则与 ``/api/admin/contacts`` 完全一致（后端同一份实现）：
企业名必须在官方名单内、电话不得是个人手机号、官网必须 http(s)、邮箱必须合法。
任何一行不合法都会报错并列出原因，**不会写入半成品**。
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(ROOT / "tools"))

from app.services.contacts import EMAIL_RE, MOBILE_RE, load_contacts  # noqa: E402
from companies import load_companies  # noqa: E402

CSV_PATH = ROOT / "data" / "curated" / "manual_sites_template.csv"
MANUAL_PATH = ROOT / "data" / "curated" / "manual_sites.json"

COLUMNS = [
    "name", "list_names", "city", "district",
    # 只读上下文：名单里带的属性（含核实档位），供人工判断时参考
    "verify_tier", "products", "current_founded_year", "current_registered_capital",
    # 只读上下文：现有（自动发现）结果，供填写时参考
    "current_website", "current_phone", "current_email", "current_source",
    # 可填写列：留空即不覆盖；填写后按「人工补充 · 站方核对」入库
    "website", "public_phone", "public_email", "address", "source_url", "note",
    # 企业属性也可以人工补：这两列是本次新增字段
    "founded_year", "registered_capital",
]
EDITABLE = [
    "website", "public_phone", "public_email", "address", "source_url", "note",
    "founded_year", "registered_capital",
]


def export(status: str = "empty") -> int:
    companies = load_companies()
    contacts = load_contacts()
    skeleton = {c["name"]: c for c in companies}
    rows = []
    for company in companies:
        record = contacts.get(company["name"], {})
        current = record.get("contact_status", "empty")
        if status != "all" and current != status:
            continue
        rows.append(
            {
                "name": company["name"],
                "list_names": " / ".join(company["list_names"]),
                "city": company["city"],
                "district": company["district"],
                "verify_tier": skeleton[company["name"]].get("verify_tier", ""),
                "products": " / ".join(skeleton[company["name"]].get("products", [])),
                "current_founded_year": skeleton[company["name"]].get("founded_year") or "",
                "current_registered_capital": skeleton[company["name"]].get("registered_capital", ""),
                "current_website": record.get("website", ""),
                "current_phone": record.get("public_phone", ""),
                "current_email": record.get("public_email", ""),
                "current_source": record.get("sources", {}).get("website", ""),
                # 可填写列一律留空：避免把自动发现的结果当成本次人工补充重新入库，
                # 否则会把来源标签从「自动发现」错误地升格为「人工补充」。
                "website": "",
                "public_phone": "",
                "public_email": "",
                "address": "",
                "source_url": "",
                "note": "",
                "founded_year": "",
                "registered_capital": "",
            }
        )

    CSV_PATH.parent.mkdir(parents=True, exist_ok=True)
    with CSV_PATH.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=COLUMNS)
        writer.writeheader()
        writer.writerows(rows)
    print(f"导出 {len(rows)} 行（contact_status={status}）→ {CSV_PATH}")
    print("current_* 列是现有自动结果，仅供参考、不会被回写；")
    print(
        "请只填 website / public_phone / public_email / address / source_url / note "
        "/ founded_year / registered_capital。"
    )
    print("填写提示：只填公开信息；电话填总机/400，不要填个人手机号。")
    return 0


def read_rows() -> list[dict]:
    with CSV_PATH.open("r", encoding="utf-8-sig", newline="") as handle:
        return [row for row in csv.DictReader(handle)]


def validate(rows: list[dict], known: set[str]) -> list[str]:
    problems: list[str] = []
    for index, row in enumerate(rows, 2):  # 2 = 首个数据行的行号
        name = (row.get("name") or "").strip()
        filled = {field: (row.get(field) or "").strip() for field in EDITABLE}
        if not any(filled.values()):
            continue  # 未填写的行直接跳过
        if name not in known:
            problems.append(f"第 {index} 行：「{name}」不在官方名单中（不会生效）")
            continue
        if not (filled["website"] or filled["public_phone"] or filled["public_email"] or filled["address"]):
            problems.append(f"第 {index} 行：「{name}」只填了来源/备注，至少要有官网/电话/邮箱/地址之一")
        if filled["website"] and not filled["website"].startswith(("http://", "https://")):
            problems.append(f"第 {index} 行：「{name}」官网需以 http:// 或 https:// 开头")
        if filled["public_phone"] and MOBILE_RE.search(filled["public_phone"]):
            problems.append(f"第 {index} 行：「{name}」电话是个人手机号，本站不采集个人联系方式")
        if filled["public_email"] and not EMAIL_RE.match(filled["public_email"].lower()):
            problems.append(f"第 {index} 行：「{name}」邮箱格式不合法")
        if filled["source_url"] and not filled["source_url"].startswith(("http://", "https://")):
            problems.append(f"第 {index} 行：「{name}」来源链接需以 http:// 或 https:// 开头")
        year = filled["founded_year"]
        if year and not (re.fullmatch(r"(19|20)\d{2}", year) and 1949 <= int(year) <= 2100):
            problems.append(f"第 {index} 行：「{name}」成立年份需为 1949-2100 之间的四位年份")
        capital = filled["registered_capital"]
        if len(capital) > 64:
            problems.append(f"第 {index} 行：「{name}」注册资本过长（≤64 字符）")
    return problems


def import_rows(dry_run: bool = False) -> int:
    known = {c["name"] for c in load_companies()}
    rows = read_rows()
    problems = validate(rows, known)
    if problems:
        print(f"校验未通过（{len(problems)} 处），未写入任何内容：")
        for problem in problems[:60]:
            print("  -", problem)
        if len(problems) > 60:
            print(f"  ... 另有 {len(problems) - 60} 处")
        return 1

    payload = {"_note": "手工补充的企业公开信息（由 tools/manual_csv.py 或 /api/admin/contacts 维护）",
               "companies": []}
    if MANUAL_PATH.exists():
        try:
            payload = json.loads(MANUAL_PATH.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            pass
    companies = payload.setdefault("companies", [])
    by_name = {row.get("name"): index for index, row in enumerate(companies)}

    added = updated = skipped = 0
    for row in rows:
        name = (row.get("name") or "").strip()
        filled = {field: (row.get(field) or "").strip() for field in EDITABLE}
        if not name or not any(filled.values()):
            skipped += 1
            continue
        filled["public_email"] = filled["public_email"].lower()
        if name in by_name:
            index = by_name[name]
            companies[index] = {**companies[index], **{k: v for k, v in filled.items() if v}}
            updated += 1
        else:
            companies.append({"name": name, **filled})
            by_name[name] = len(companies) - 1
            added += 1

    if dry_run:
        print(f"试运行：新增 {added} · 更新 {updated} · 跳过（未填）{skipped}；未写入文件")
        return 0

    MANUAL_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"已写入 {MANUAL_PATH}：新增 {added} · 更新 {updated} · 跳过（未填）{skipped}")
    print("下一步：cd backend && ../.venv/bin/python -m app.cli seed")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    exp = sub.add_parser("export", help="导出待补充清单为 CSV")
    exp.add_argument("--status", default="empty",
                     choices=["empty", "partial", "full", "all"], help="导出哪一类企业")
    imp = sub.add_parser("import", help="把填写好的 CSV 写回 manual_sites.json")
    imp.add_argument("--dry-run", action="store_true", help="只校验不写入")
    args = parser.parse_args()

    if args.command == "export":
        return export(args.status)
    return import_rows(args.dry_run)


if __name__ == "__main__":
    raise SystemExit(main())