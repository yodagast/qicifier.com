"""Prune the curated site seed so it only contains companies from the official lists.

``sites_seed.json`` is 站方整理: candidates may only exist for companies that are
actually in the two source lists (小巨人公示名单 + 浙江制造精品目录). Anything else
would create an enterprise record with no official basis, which the directory
explicitly forbids.

Usage::

    python tools/prune_seed.py            # report only
    python tools/prune_seed.py --apply    # rewrite the seed file
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import enrich_companies as E  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    path = ROOT / "data" / "curated" / "sites_seed.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    known = {c["name"] for c in E.load_companies()}

    kept, dropped = [], []
    for row in payload["companies"]:
        (kept if row["name"] in known else dropped).append(row)

    print(f"kept={len(kept)} dropped={len(dropped)}")
    for row in dropped:
        print("  drop:", row["name"])

    if args.apply:
        payload["companies"] = kept
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"rewrote {path}")

        results_path = ROOT / "data" / "curated" / "enriched.json"
        if results_path.exists():
            rows = json.loads(results_path.read_text(encoding="utf-8"))
            survivors = [r for r in rows if r["name"] in known]
            results_path.write_text(
                json.dumps(sorted(survivors, key=lambda r: r["name"]), ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            print(f"pruned enriched.json: {len(rows)} -> {len(survivors)}")


if __name__ == "__main__":
    main()