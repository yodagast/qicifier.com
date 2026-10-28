"""浙江第四批产业集群「全国代表企业」清单 → 标准产物（离线等价物）。

清单本身通过接口导入：``POST /api/admin/lists/import``。本脚本走**同一条代码路径**
（``app.services.list_import``），只是不需要起服务，适合在做完数据改动后重跑一次、
或者在没有后端进程时校验产物。

    python tools/parse_cluster_reps.py            # 重新生成三份产物
    python tools/parse_cluster_reps.py --dry-run  # 只打印报告，不落盘

来源：``docs/浙江第四批产业集群_全国代表企业清单_副本.csv``（387 行 = 第一批 161 + 第二批 226），
方法论见同目录的调研 HTML。原件 ``docs/浙江第四批产业集群_全国代表企业清单.csv`` 的 161 行
与副本的第一批**逐字段完全一致**，已被副本取代。

这一份和前面两份调研清单**性质不同**：

* 前两份是「一个产区的企业名录」，自己带一个新的产业群；
* 这一份是**给已有的官方集群配代表企业** —— 每一条都对应我们库里已经建档的官方集群
  （8 核心区 / 7 协同区 / 20 “新星”产业群），所以它不新建集群，只往 enterprise_cluster 里加归属。

清单里这些字段问题都必须处理（规则集中在 ``services/list_import.py``）：

**① `官网类型` 决定这个链接能不能当「官网」。** 只有企业自有站点（企业官网 / 科研机构官网 /
品牌站 / 品牌海外站）与集团、母公司站点能进 ``website``（后者带 ``website_kind`` 标注）。
「第三方商铺」页填进官网字段，采购商点进去看到的是中间平台，不是工厂本身。
未识别的新类型**一律不发布**并报出来，因为「没见过的值默认当成企业官网」等于替清单做判断。

**② 电话是分号分隔的多个号码，且夹杂手机号。** 常见写法为
``0576-87212222；0576-87370888``；另有行第二个号码是个人手机、或 ``0574-8749（尾号待补）``
这种残号。取第一个合法座机/400，手机号一律不收录并把原因写进备注。

**③ 哨兵值会以「有官网」的形态进来。** ``待补`` / ``无独立官网`` / ``1688店铺``
都是「没有」的意思，``if website:`` 会把它们全当成有官网。哨兵值里的线索
（``待补（杭钢集团 hzsteel.com）``）原样写进备注，不揪出来填进官网字段。

**④ 企业名常带括注。** 387 行里 119 行带股票代码/品牌名（``（002230）``、``（BrainCo）``）。
验收门拿这种名字去页面里找永远匹配不上，所以另存 ``match_name``（只剥**结尾**括注，
``中航（成都）无人机系统股份有限公司`` 里的括注是法定名称的一部分，必须保留）。

**⑤ 「全国代表」意味着有省外企业。** 54 家注册地/办公地在省外，它们是作为该赛道的
全国标杆被引用的。这些标 ``out_of_province``，不参与浙江产区统计。

**⑥ 企业所在地要从它自己的地址认。** 「集群（县市区）」是**集群**的位置，不是企业的位置 ——
科大讯飞挂在余杭的集群上，人在合肥。
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.services.list_import import (  # noqa: E402
    MOBILE_SHAPE,
    WEBSITE_SENTINELS,
    import_list,
)

CSV_PATH = ROOT / "docs" / "浙江第四批产业集群_全国代表企业清单_副本.csv"
SOURCE_KEY = "cluster_reps"
LIST_NAME = "浙江第四批产业集群全国代表企业清单（第三方调研）"
USED_FOR = (
    "为已建档的官方集群（8 核心区 / 7 协同区 / 20 个“新星”产业群）各配 6-7 家代表企业，"
    "共 387 条 / 385 家（第一批 161 条 + 第二批 226 条；深圳市兆驰、西安铂力特各代表两个集群，"
    "各占两行；其中 54 家为省外「全国代表」，单独标记、不参与浙江统计）；"
    "逐行保留调研方核实档位（A 官网公示 180 / B 工商可核 184 / C 第三方线索 23）；"
    "`官网类型` 如实保留：集团官网与母公司官网会标注链接类型，"
    "第三方商铺页不计入官网字段，未识别的新类型一律不发布"
)

# 这一份清单的预期形态。数值变了就是数据变了，必须有人看一眼再改 ——
# 「断言驱动」在这里是唯一能防住「悄悄少了一半企业」的东西。
EXPECTED = {
    "rows": 387,
    "companies": 385,
    "with_website": 276,
    "with_phone": 239,
    "out_of_province": 54,
    "annotated_names": 119,
}
EXPECTED_TIERS = {"A 官网公示": 180, "B 工商可核": 184, "C 第三方线索": 23}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="只解析并打印报告，不落盘")
    args = parser.parse_args()

    text = CSV_PATH.read_text(encoding="utf-8-sig")
    result = import_list(
        text=text,
        list_name=LIST_NAME,
        grade="site_curated",
        source_key=SOURCE_KEY,
        source_file=f"docs/{CSV_PATH.name}",
        used_for=USED_FOR,
        dry_run=args.dry_run,
    )
    report = result["report"]
    records = result["parsed"]["rows"]

    for key, expected in EXPECTED.items():
        assert report[key] == expected, f"{key} 应为 {expected}，实际 {report[key]}"
    assert report["tiers"] == EXPECTED_TIERS, f"档位分布异常: {report['tiers']}"
    assert report["duplicates"] == {
        "深圳市兆驰股份有限公司（002429）": 2,
        "西安铂力特增材技术股份有限公司（688333）": 2,
    }, report["duplicates"]
    # 哨兵值绝不能进官网/电话字段
    assert all(not any(s in r["website"] for s in WEBSITE_SENTINELS) for r in records)
    assert all(
        MOBILE_SHAPE.fullmatch(re.sub(r"\D", "", r["phone"])) is None for r in records
    ), "电话字段里出现了手机号"
    assert not report["unknown_website_kinds"], report["unknown_website_kinds"]
    # 结尾的股票代码/品牌名括注要摘掉，中间的（中航（成都）…）要保留
    assert all(r["name"].startswith(r["match_name"]) for r in records)

    print(json.dumps({k: v for k, v in report.items() if k not in ("outputs", "entry")}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
