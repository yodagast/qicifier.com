"""Parse 《第四批省级重点产业集群核心区协同区和"新星"产业群公示名单》.

Source: docs/第四批省级重点产业集群核心区协同区和“新星”产业群公示名单.pdf
        （浙江省经济和信息化厅，2026-09-23 公示；公告页
         https://jxt.zj.gov.cn/col/col1229892974/art/2026/art_f0a2cbb8f7e64b608edb8f25794b04f3.html）

This is the "主骨架" the research plan asks for (方案 §01): 官方发布、分批更新、
能落到具体县（市、区）。Three sections:

* 一、核心区（8 个）        —— 县（市、区）/开发区 + 所属产业集群
* 二、协同区（7 个）        —— 同上
* 三、"新星"产业群（22 个）  —— 县（市、区）/开发区 + 所属产业高地 + 细分赛道

Mapping decision (方案 §08 陷阱 2「核心区不是地理边界」): the cluster here is the
**认定主体**, so it maps onto our ``cluster_region`` rows with ``role = 核心区 /
协同区``, not onto a new cluster entity. The "新星" groups have their own
细分赛道 (e.g. 光电（半导体）), so they become their own ``cluster`` rows at tier B.

Output: data/source/zhejiang_clusters_batch4.json
"""

from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PDF = ROOT / "docs" / "第四批省级重点产业集群核心区协同区和“新星”产业群公示名单.pdf"
OUT = ROOT / "data" / "source" / "zhejiang_clusters_batch4.json"

LIST_NAME = "第四批省级重点产业集群核心区协同区和“新星”产业群公示名单"
PUBLISHER = "浙江省经济和信息化厅"
PUBLISHED_AT = "2026-09-23"
SOURCE_URL = (
    "https://jxt.zj.gov.cn/col/col1229892974/art/2026/"
    "art_f0a2cbb8f7e64b608edb8f25794b04f3.html"
)

EXPECTED = {"核心区": 8, "协同区": 7, "新星产业群": 22}

# 归属的「产业高地」四类（新星产业群用）
HIGHLANDS = ("新一代信息技术", "高端装备", "现代消费与健康", "绿色石化与新材料")

DISTRICT_RE = re.compile(
    r"^(杭州市|宁波市|温州市|嘉兴市|湖州市|绍兴市|金华市|衢州市|舟山市|台州市|丽水市)"
    r"([\u4e00-\u9fa5]{1,10}?(?:县|市|区|新区|开发区))"
    r"\s*(?=[\u4e00-\u9fa5]{2,})"
)
SECTION_RE = re.compile(r"[一二三]、\s*(核心区|协同区|“新星”产业群)\s*（(\d+)个）")


def extract_text(path: Path) -> str:
    import PyPDF2

    reader = PyPDF2.PdfReader(str(path))
    return "".join(page.extract_text() or "" for page in reader.pages)


def clean(text: str) -> str:
    text = text.replace("\ufeff", "")
    text = re.sub(r"^1附件1\s*", "", text)
    text = text.replace("第四批省级重点产业集群核心区协同区和", "")
    text = text.replace("“新星”产业群公示名单", "")
    text = re.sub(r"序号县（市、区）/(?:开发区|开)?\s*\n?（?(?:园区|发区（园区）)?所属(?:产业集群|产业高地)\s*细分赛道?", "\n", text)
    text = re.sub(r"(\d)([一二三]、)", r"\1\n\2", text)  # 页码/序号与下一个标题粘连
    # 单元格内换行（"台州市天台经济" / "开发区高端装备 仪器仪表"）→ 合并
    lines = text.split("\n")
    merged: list[str] = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        if merged and not re.match(r"^\d{1,2}\s*[\u4e00-\u9fa5]", line) and not SECTION_RE.search(line):
            merged[-1] += line
        else:
            merged.append(line)
    return re.sub(r"[ \t]+", " ", "\n".join(merged))


def split_sections(text: str) -> dict[str, list[str]]:
    sections: dict[str, list[str]] = {}
    current: str | None = None
    for line in text.split("\n"):
        match = SECTION_RE.search(line)
        if match:
            current = "新星产业群" if "新星" in match.group(1) else match.group(1)
            sections[current] = []
            continue
        if current:
            sections[current].append(line)
    return sections


def parse_rows(rows: list[str], with_track: bool) -> list[dict]:
    parsed: list[dict] = []
    for row in rows:
        match = re.match(r"^(\d{1,2})\s*(.+)$", row)
        if not match:
            continue
        seq, body = int(match.group(1)), match.group(2)
        district_match = DISTRICT_RE.match(body)
        if not district_match:
            continue
        city, district = district_match.group(1), district_match.group(2)
        remainder = body[district_match.end():].strip()

        if with_track:
            highland = next((h for h in HIGHLANDS if remainder.startswith(h)), "")
            track = remainder[len(highland):].strip() if highland else ""
        else:
            highland = ""
            track = remainder
        track = re.sub(r"\d+$", "", track).strip()  # 页码粘连在集群名末尾

        parsed.append(
            {
                "list_name": LIST_NAME,
                "publisher": PUBLISHER,
                "published_at": PUBLISHED_AT,
                "source_url": SOURCE_URL,
                "seq": seq,
                "city": city,
                "district": district,
                "cluster": track,
                "highland": highland,
            }
        )
    return parsed


def parse(text: str) -> dict:
    sections = split_sections(clean(text))
    core = parse_rows(sections.get("核心区", []), with_track=False)
    synergy = parse_rows(sections.get("协同区", []), with_track=False)
    newstar = parse_rows(sections.get("新星产业群", []), with_track=True)

    for name, rows in (("核心区", core), ("协同区", synergy), ("新星产业群", newstar)):
        expected = EXPECTED[name]
        if len(rows) != expected:
            raise SystemExit(f"{name} 行数不符：解析到 {len(rows)}，公告声明 {expected}")
        seqs = sorted(r["seq"] for r in rows)
        if seqs != list(range(1, expected + 1)):
            raise SystemExit(f"{name} 序号不连续：{seqs}")

    return {"核心区": core, "协同区": synergy, "新星产业群": newstar}


def main() -> None:
    payload = parse(extract_text(PDF))
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    for name, rows in payload.items():
        print(f"{name}: {len(rows)}")
        for row in rows:
            track = row["cluster"]
            highland = f" [{row['highland']}]" if row["highland"] else ""
            print(f"  {row['seq']:2d} {row['city']}{row['district']:10s} {track}{highland}")
    print(f"-> {OUT}")


if __name__ == "__main__":
    main()