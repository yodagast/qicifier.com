"""Parse 第七批专精特新"小巨人"企业公示名单 into structured JSON.

Source: docs/1第七批专精特新“小巨人”企业公示名单pdf.pdf (附件1)
Output: data/source/xiaojuren.json
"""

from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PDF_DIR = ROOT / "docs"
OUT = ROOT / "data" / "source" / "xiaojuren.json"


def extract_text(path: Path) -> str:
    import PyPDF2

    reader = PyPDF2.PdfReader(str(path))
    return "".join(page.extract_text() or "" for page in reader.pages)


def parse(text: str) -> list[dict]:
    text = text.replace("\ufeff", "")
    text = re.sub(r"第\s*\d+\s*页，共\s*\d+\s*页序号省\(区、市）\s*企业名称", "", text)
    text = re.sub(r"第\s*\d+\s*页，共\s*\d+\s*页", "", text)

    pattern = re.compile(
        r"(\d{1,4})\s*(浙江省|浙江)\s*(.+?)(?=\n\s*\d{1,4}\s*(?:浙江省|浙江)|$)",
        re.S,
    )
    rows: list[dict] = []
    for match in pattern.finditer(text):
        name = match.group(3).replace("\n", "").strip()
        if not name:
            continue
        rows.append(
            {
                "list_name": "第七批专精特新“小巨人”企业公示名单",
                "seq": int(match.group(1)),
                "province": "浙江省",
                "name": name,
            }
        )
    return rows


def main() -> None:
    pdf = next(p for p in PDF_DIR.glob("*小巨人*"))
    rows = parse(extract_text(pdf))
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"{len(rows)} rows -> {OUT}")


if __name__ == "__main__":
    main()