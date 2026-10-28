"""CLI for the contact layer (merge implementation lives in the backend package).

``python tools/contacts.py``               报告合并结果 + 手工补充校验
``python tools/contacts.py --list-empty``  列出仍无任何联系方式的企业
``python tools/contacts.py --template``    输出可粘贴进 manual_sites.json 的骨架
``python tools/contacts.py --json``        输出合并后的完整结果
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(ROOT / "tools"))

from app.services.contacts import load_contacts, validate_manual  # noqa: E402
from companies import load_companies  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--list-empty", action="store_true", help="列出无联系方式的企业")
    parser.add_argument("--template", action="store_true", help="输出手工补充骨架")
    parser.add_argument("--json", action="store_true", help="以 JSON 输出合并结果")
    args = parser.parse_args()

    companies = load_companies()
    known = {c["name"] for c in companies}
    contacts = load_contacts()
    empty = [
        c["name"] for c in companies
        if contacts.get(c["name"], {}).get("contact_status", "empty") == "empty"
    ]

    if args.template:
        print(json.dumps(
            {"companies": [
                {"name": name, "website": "", "public_phone": "", "public_email": "",
                 "address": "", "source_url": "", "note": ""}
                for name in empty
            ]},
            ensure_ascii=False, indent=2,
        ))
        return 0

    if args.list_empty:
        for name in empty:
            print(name)
        return 0

    if args.json:
        print(json.dumps(contacts, ensure_ascii=False, indent=2))
        return 0

    filled = [r for r in contacts.values() if r.get("contact_status") != "empty"]
    full = sum(1 for r in filled if r["contact_status"] == "full")
    print(f"企业总数   : {len(companies)}")
    print(f"有联系方式 : {len(filled)}  (官网+联系方式齐全 {full} · 部分 {len(filled) - full})")
    print(f"仍为空     : {len(empty)}")

    by_layer: dict[str, int] = {}
    for record in filled:
        for layer in record.get("layers", []):
            by_layer[layer] = by_layer.get(layer, 0) + 1
    print(f"来源分布   : {by_layer}")

    problems = validate_manual(known)
    if problems:
        print("\n手工补充校验问题：")
        for problem in problems:
            print("  -", problem)
        return 1
    print("\nmanual_sites.json 校验通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())