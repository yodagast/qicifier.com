"""Parse 《2026年浙江制造精品推广目录》公示名单 (docs/产业群名单.pdf) into structured JSON.

Layout is column-based: 序号 | “415X”集群赛道 | 企业名称 | 产品名称及型号 | 所属地区（推荐单位）
The "赛道" is only printed on the first row of each block, so it has to be carried down.
Extracted text flattens columns, so each record is heuristically split into
  <seq> <*企业名称> <产品名> <地区>
by locating the company-name token and the trailing region token.

Output: data/source/zhejiang_mingpin_2026.json
"""

from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PDF_DIR = ROOT / "docs"
OUT = ROOT / "data" / "source" / "zhejiang_mingpin_2026.json"

LIST_NAME = "《2026年浙江制造精品推广目录》公示名单"

# “415X” 千亿级集群赛道 —— 名称取自《2026年浙江制造精品推广目录》公示名单原文
# （共 15 个赛道，与 415X 体系的 15 个千亿级集群一一对应）。文本层会把赛道名
# 拆成两行（"低空经济与" / "航空航天浙江石化阀门有限公司…"），所以按前缀匹配。
TRACKS = [
    "新能源汽车及零部件",
    "智能物联",
    "人工智能",
    "集成电路",
    "高端新材料",
    "现代纺织与服装",
    "新能源与新型储能",
    "绿色石化",
    "生物医药与医疗器械",
    "低空经济与航空航天",
    "高端软件",
    "工业母机与机器人",
    "电气与农机装备",
    "高端船舶与海工装备",
    "现代家居与智能家电",
]

TRACK_ALIASES: dict[str, str] = {}
TRACK_PATTERN = "|".join(sorted(TRACKS, key=len, reverse=True))
INLINE_TRACK_RE = re.compile(TRACK_PATTERN + r"(?=[\u4e00-\u9fa5A-Za-z(（])")

CITY_RE = re.compile(
    r"(杭州市|宁波市|温州市|嘉兴市|湖州市|绍兴市|金华市|衢州市|舟山市|台州市|丽水市)"
)
CITY_ALL = re.compile(
    r"杭州市|宁波市|温州市|嘉兴市|湖州市|绍兴市|金华市|衢州市|舟山市|台州市|丽水市"
)
DISTRICT_RE = re.compile(
    r"(?:杭州市|宁波市|温州市|嘉兴市|湖州市|绍兴市|金华市|衢州市|舟山市|台州市|丽水市)"
    r"([\u4e00-\u9fa5]{1,8}?"
    r"(?:区|县|市|新区|经开区|高新区|开发区|智造新城|新城))"
    r"$"
)
COMPANY_RE = re.compile(r"[\u4e00-\u9fa5A-Za-z0-9（）()·\-]{2,40}?"
    r"(?:有限公司|股份有限公司|集团股份有限公司|集团有限公司|公司|研究院|集团)")

FOOTER_RE = re.compile(r"第\s*\d+\s*页，共\s*\d+\s*页")
HEADER_RE = re.compile(r"序号“415X”集群\s*赛道企业名称\s*产品名称及型号所属地区\s*（推荐单位）")
NOTE_RE = re.compile(r"^（省[\u4e00-\u9fa5]+推荐）$")
# Same notes, but pasted onto the end of a record line by the text layer.
NOTE_RE_G = re.compile(r"（省[\u4e00-\u9fa5]+推荐）")


def extract_text(path: Path) -> str:
    import PyPDF2

    reader = PyPDF2.PdfReader(str(path))
    return "".join(page.extract_text() or "" for page in reader.pages)


def clean_text(text: str) -> str:
    text = text.replace("\ufeff", "")
    text = re.sub(r"^1附件2\s*", "", text)
    text = re.sub(r"^《2026年浙江制造精品推广目录》公示名单\s*", "", text)
    text = NOTE_RE_G.sub("", text)  # 推荐单位 notes are appended mid-line
    text = FOOTER_RE.sub("\n", text)
    text = re.sub(r"序号“415X”集群", "\n", text)
    text = re.sub(r"赛道企业名称\s*产品名称及型号所属地区", "\n", text)
    text = text.replace("（推荐单位）", "\n")
    text = re.sub(r"\d+(?=序号)", "", text)  # page number glued to a header
    # 序号与赛道名可能被粘到上一行末尾（"…金华市金东区169绿色石化浙江捷发科技…"）
    text = re.sub(r"(?<=\D)(\d{1,3})(?=" + TRACK_PATTERN + r")", r"\n\1", text)
    # 把"被拆成两行"的续行并回上一行。"低空经济与" + "航空航天浙江石化阀门有限公司…"
    # 这类由下面的合并逻辑统一处理：非序号开头的行一律视为上一行的续行。
    lines = text.split("\n")
    merged: list[str] = []
    for line in lines:
        if merged and line and not re.match(r"^\d{1,4}(?:\s|$)", line):
            merged[-1] += line
        else:
            merged.append(line)
    text = "\n".join(merged)
    text = re.sub(r"[ \t]+", " ", text)
    return text


def detect_track(line: str) -> str | None:
    """Return the track name a line starts with, or None.

    A line may be a bare (possibly truncated) track name, or a record line whose
    text layer glued the track name in front of the company name.
    """
    match = INLINE_TRACK_RE.match(line)
    if match:
        return TRACK_ALIASES.get(match.group(0), match.group(0))
    if len(line) <= 12:
        for track in TRACKS:
            if track == line or track.startswith(line):
                return track
    return None


def strip_track(line: str, track: str) -> str:
    """Remove a leading track name (possibly truncated by a line break) from `line`."""
    if line.startswith(track):
        return line[len(track):].strip()
    for alias, canonical in TRACK_ALIASES.items():
        if canonical == track and line.startswith(alias):
            return line[len(alias):].strip()
    for cut in range(len(track) - 1, 1, -1):  # truncated prefix of the track
        if line.startswith(track[:cut]):
            return line[cut:].strip()
    return line


def iter_blocks(text: str):
    """Yield (seq, remainder, track) for every record.

    The text layer sometimes puts the 序号 alone on its own line, so a pending
    sequence number is carried over to the next non-empty line.
    """
    lines = text.split("\n")
    track: str | None = None
    pending_seq: int | None = None
    for line in lines:
        line = line.strip()
        if not line:
            continue
        if NOTE_RE.match(line):
            continue

        if re.fullmatch(r"\d{1,4}", line):
            pending_seq = int(line)
            continue

        seq: int | None = pending_seq
        pending_seq = None
        body = line
        match = re.match(r"^(\d{1,4})\s*(.*)$", line)
        if match and match.group(2):
            seq = int(match.group(1))
            body = match.group(2)

        inline = detect_track(body)
        if inline:
            track = inline
            body = strip_track(body, inline)
        if not body:
            continue
        yield seq, body, track


def split_record(remainder: str) -> tuple[str, str, str]:
    """Split a flattened record into (company, product, region)."""
    region = ""
    tail_region = re.search(
        r"((?:杭州市|宁波市|温州市|嘉兴市|湖州市|绍兴市|金华市|衢州市|舟山市|台州市|丽水市)"
        r"[\u4e00-\u9fa5]{0,8}?(?:区|县|市|新区|经开区|高新区|开发区|新城|智造新城)"
        r"|景宁畲族自治县)$",
        remainder,
    )
    if tail_region:
        region = tail_region.group(1)
        remainder = remainder[: tail_region.start()]

    company_match = COMPANY_RE.search(remainder)
    if not company_match:
        return "", remainder.strip(), region
    company = company_match.group(0)
    product = remainder[company_match.end():].strip()
    # The text layer sometimes drops the 所属地区 column into the middle of the
    # product name (e.g. "（FJH-391、FJH-340）衢州市智造新城").
    if not region:
        mid = CITY_ALL.search(product)
        if mid:
            region = product[mid.start():].strip()
            product = product[: mid.start()].strip()
    region = re.sub(r"\d+$", "", region).strip()  # stray page number glued to 地区
    return company, product, region


def parse(text: str) -> list[dict]:
    rows: list[dict] = []
    for seq, remainder, track in iter_blocks(text):
        company, product, region = split_record(remainder)
        if not company:
            continue
        city = ""
        district = ""
        if region:
            city_match = CITY_RE.match(region)
            city = city_match.group(1) if city_match else ""
            district_match = DISTRICT_RE.match(region)
            district = district_match.group(1) if district_match else ""
        rows.append(
            {
                "list_name": LIST_NAME,
                "seq": seq,
                "track": track or "",
                "name": company,
                "product": product,
                "region_raw": region,
                "city": city,
                "district": district,
            }
        )
    return rows


def main() -> None:
    pdf = PDF_DIR / "产业群名单.pdf"
    rows = parse(clean_text(extract_text(pdf)))
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    missing_company = [r for r in rows if not r["name"]]
    print(f"{len(rows)} rows -> {OUT}; unnamed={len(missing_company)}")
    for r in rows[:5]:
        print(r)


if __name__ == "__main__":
    main()