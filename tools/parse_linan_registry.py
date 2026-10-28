"""临安区规上企业基本信息（政府公开数据）→ 标准产物。

    python tools/parse_linan_registry.py            # 生成三份产物
    python tools/parse_linan_registry.py --dry-run  # 只打印报告，不落盘

来源：``docs/临安区规上企业基本信息20260731.csv``（594 行，2026-07-31 导出）。

**这份和前面五份都不是一回事。** 前五份是「调研清单」，这一份是
**政府公开的企业底表**：由区级市场监管/统计口径导出，逐条给的是
统一社会信用代码、街道、行业代码。

| 它有 | 它没有 |
| --- | --- |
| 企业名称、成立日期、经营状态、所属街道、行业代码与名称、区级产业分组 | **官网、电话、地址、联系人** |

这决定了三件事：

**① 它是「谁存在」的证据，不是「怎么联系」的来源。**
字段只用来确认主体与归属；联系方式一律留空，由后面的官网发现与人工补充去填。
把一张没有电话的表导入成「有联系方式」，比不导更糟。

**② 档位是 `gov_open`（政府公开信息），不是 `official`（官方认定）。**
它来自政府，但不是「认定名单」——「规上企业」是统计口径（年主营业务收入达标），
不是产业政策认定。两者混用会让「官方认定」这个标签贬值。

**③ 产区归属从「所属街道 × 产业名称」的交叉表里读出来，不猜。**
这份表逐行给了街道与区级产业分组，两者的交叉就是产区：
「临安电线电缆」= 电线电缆产业的 58 家（玲珑街道 35 家是最集中的）。
13 个产区档案已经在 `build_seed.py` 里建好（含主镇街与范围），这里只做映射 ——
原表 14 个分组里有 2 个（装备制造、其中高端装备）同属青山湖科技城高端装备这一条产线。

**不收录的东西**（原表里有，但我们不采）：

* `法人姓名`（已打码成「张**」）—— 仍然是人名，且与业务无关；
* `组织机构代码`（已打码）—— 同上，且统一社会信用代码更有用；
* `创建人 / 更新人 / 创建时间 / 更新时间 / 前置库_入库时间` —— 是数据库维护痕迹，
  不是企业信息，写进页面只会让人以为那是核验记录。

**唯一不采的企业行**：经营状态为「注销」的那 1 家（`杭州临安天目铸造有限公司`）——
规上企业底表里留着它是因为它当年在库，但它已经不是经营中的企业。
不采它，且在报告里点名，这样这个决定是看得见的。

**两处数据瑕疵**（原表如此，修法写在代码里）：

* `产业代码` / `产业名称` 尾部带逗号（``"1.1,"``），是导出时的拼接残留；
* 有 17 行的 `所属行业名称` / `行业代码` 为空 —— 行业未知不影响主体与归属。
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.services.list_import import import_list  # noqa: E402

CSV_PATH = ROOT / "docs" / "临安区规上企业基本信息20260731.csv"
SOURCE_KEY = "linan_registry"
LIST_NAME = "临安区规上企业基本信息（政府公开数据）"
GRADE = "gov_open"

USED_FOR = (
    "杭州市临安区 594 家规模以上工业企业（2026-07-31 导出的政府公开底表），"
    "作为该区 13 个产区（临安电线电缆、临安装饰纸、临安山核桃、临安节能灯、"
    "太阳五金工具、青山湖科技城高端装备、临安新材料、临安纺织服装、临安电子电路、"
    "临安生物医药、临安新能源、临安集成电路、临安精密元器件）的**主体名单**；"
    "产区归属由原表的「所属街道 × 产业名称」交叉确定，不是推断出来的。"
    "这张表**只有主体信息**（企业名称、统一社会信用代码、成立日期、经营状态、"
    "所属街道、行业代码与名称、区级产业分组），没有官网、电话、地址 —— "
    "联系方式留空，另行核实或人工补充，不会因为导入这张表就凭空出现。"
    "档位是「政府公开信息」而不是「官方认定」：规上企业是统计口径（年主营业务收入达标），"
    "不是产业政策认定。已排除 1 家经营状态为「注销」的企业；"
    "法人姓名与组织机构代码虽然已打码，仍属个人信息，一律不采集"
)

EXPECTED = {
    "rows": 593,          # 594 − 1 家注销
    "companies": 593,
    "with_website": 0,
    "with_phone": 0,
    "with_email": 0,
    "with_address": 0,
    "out_of_province": 0,
}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="只解析并打印报告，不落盘")
    args = parser.parse_args()

    result = import_list(
        text=CSV_PATH.read_text(encoding="utf-8-sig"),
        list_name=LIST_NAME,
        grade=GRADE,
        source_key=SOURCE_KEY,
        source_file=f"docs/{CSV_PATH.name}",
        used_for=USED_FOR,
        dry_run=args.dry_run,
    )
    report = result["report"]
    records = result["parsed"]["rows"]

    for key, expected in EXPECTED.items():
        assert report[key] == expected, f"{key} 应为 {expected}，实际 {report[key]}"

    # 这张表的价值全在「主体 + 归属」，所以这两项必须完整
    assert all(r["list_cluster_slug"] for r in records), "有企业没挂上产区"
    # 13 个产区档案（`build_seed.py` 里为临安建档的那一批），
    # 对应原表 14 个区级分组中的 13 个 —— 「装备制造」与「其中高端装备」
    # 都归到青山湖科技城高端装备这一条产线上。
    assert len(report["cluster_dispatch"]) == 13, f"产区数应为 13，实际 {len(report['cluster_dispatch'])}"
    names = {r["name"] for r in records}
    assert len(names) == len(records), "有重名企业"

    # 联系方式必须一个都没有 —— 这张表里根本没有这些字段，
    # 一旦哪个字段被误当成电话/官网，就会凭空冒出一批「已收录联系方式」。
    assert not any(r["website"] or r["phone"] or r["email"] or r["address"] for r in records), \
        "这张表没有联系方式，不该解析出任何一条"

    # 个人信息字段不得出现在产物里（原表的法人姓名/组织机构代码已打码，仍不采）
    raw_keys = {k for r in records for k in r.get("raw", {})}
    assert not raw_keys & {"法人姓名", "组织机构代码", "创建人", "更新人"}, raw_keys
    assert "注销" not in json.dumps(records, ensure_ascii=False), "注销企业不该入库"

    # 主体识别信息与地区：这张表的可用价值就在这两项上
    assert all(r["credit_code"] for r in records), "统一社会信用代码缺失"
    assert all(r["founded_year"] for r in records), "成立年份缺失"
    unresolved = [r["name"] for r in records if not r["district"]]
    assert len(unresolved) <= 2, f"地区未解析的行过多（{len(unresolved)}）：{unresolved[:5]}"
    # 归属依据必须如实记录（这一份全部来自区级产业分组）
    assert {r["list_cluster_basis"] for r in records} == {"区级产业分组"}, "归属依据记录不对"

    print(json.dumps({k: v for k, v in report.items() if k not in ("outputs", "entry")},
                     ensure_ascii=False, indent=2))
    print("\n13 个产区（企业数）：")
    for slug, count in sorted(report["cluster_dispatch"].items(), key=lambda kv: -kv[1]):
        print(f"   {slug:26} {count}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())