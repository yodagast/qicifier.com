"""浙江省镇街级块状经济企业清单 → 标准产物（离线等价物）。

清单本身通过接口导入：``POST /api/admin/lists/import``。本脚本走**同一条代码路径**
（``app.services.list_import``），只是不需要起服务，适合做完数据改动后重跑一次、
或在没有后端进程时校验产物。

    python tools/parse_town_clusters.py            # 重新生成三份产物 + 并入候选官网
    python tools/parse_town_clusters.py --dry-run  # 只打印报告，不落盘

**注意**：接口导入（``POST /api/admin/lists/import``）只写三份产物，
不碰 ``discovered.json``。走接口之后再跑一次本脚本，候选域名才会并进自动发现层 ——
否则名单里有官网、页面上却显示「未核实」（联系方式合并不读 ``_sites.json``）。

来源：``docs/浙江省镇街级块状经济企业清单.csv``（264 行 / 46 个镇街块状经济），
方法论见同目录的调研 HTML。这是第五份清单，**第一次把调研单位下沉到镇街**。

这一份和前面几份的关键差别：

* **它逐行自带归属**（「地市 / 县市区 / 镇街 / 块状经济」四列），
  所以集群归属由清单决定，不靠我们按地址或产品去猜。46 个产区的名字映射到
  骨架里的集群 slug；其中 4 个是同一条产业带的另一种叫法（见 ``CLUSTER_SYNONYMS``），
  **指向已有档案而不是新建** —— 建了就是同一个产业带两条档案。
* **有 18 行是「对标龙头」**（``是否本地产区`` 写「对标龙头·非本地产区」）：
  施耐德、立讯精密、恒力石化这些是拿来对标的外省/境外龙头，不是这个镇街的企业。
  它们不参与该产区的统计，也不能让它们的地址把产区位置带跑。
  **但「非本地产区」不等于「不在浙江」**：天堂伞在萧山、均胜在宁波、
  森马与人本在温州 —— 这 4 家是浙江企业。省外与否只看企业自己的地址，
  一律记成省外就会把它们从浙江统计里划掉（这一条以前就是这么错的）。
* **「本地」与「可核」在这类清单里是两件事**：A 档只代表「官网公示」，
  不代表号码当前有效（调研文档专门写明未做回拨核验）。所以
  ``电话性质`` 一列原样带出去，页面上逐条标注号码性质。
* **电话列里混着「待补（原因）」**：括号外是哨兵、括号里才是为什么
  （「官网公示号码被打码」）。前者说明没号码，后者必须留下 ——
  丢掉它，读者看到空白只会以为是我们漏填。

预期形态写成了断言。数值变了就是数据变了，必须有人看一眼再改。
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

sys.path.insert(0, str(ROOT / "tools"))

from app.services.list_import import (  # noqa: E402
    MOBILE_SHAPE,
    WEBSITE_SENTINELS,
    ZHEJIANG_CITY_NAMES,
    import_list,
)
from enrich_companies import merge_into_discovered  # noqa: E402

ZJ_CITIES = set(ZHEJIANG_CITY_NAMES)

CSV_PATH = ROOT / "docs" / "浙江省镇街级块状经济企业清单.csv"
SOURCE_KEY = "town_clusters"
LIST_NAME = "浙江省镇街级块状经济企业清单（第三方调研）"
USED_FOR = (
    "浙江省 46 个镇街级块状经济（柳市低压电器、大唐袜业、濮院毛衫、织里童装…）各收 5-7 家代表企业，"
    # 这段文字会原样出现在 /about 的来源地图上，页面是纯文本插值 ——
    # 写 Markdown 记号（`**`、反引号）会原样显示出来，不许写。
    "共 264 家；这类产区多数从未进入任何省级重点集群名单，是政策名单之外的另一张产业地图。"
    "逐行自带归属（地市 / 县市区 / 镇街 / 块状经济），其中 15 个产区与已建档的镇街集群同名直接挂接、"
    "4 个是同一条产业带的另一种叫法（塘下汽摩配→瑞安汽摩配、崧厦伞业→上虞伞业、"
    "店口铜加工→店口五金、云和木玩→云和木制玩具）、其余新建档案；"
    "其中 18 家为「对标龙头·非本地产区」（施耐德、立讯精密、恒力石化、新秀丽等），"
    "单独标记、不参与该产区的统计，其中 4 家（天堂伞、均胜、森马、人本）企业本身在浙江，"
    "按地址单独记一类，不划出浙江统计。"
    "逐行保留调研方核实档位（A 官网公示 79 / B 工商可核 133 / C 第三方线索 40 / 对标龙头未标档位 12）；"
    "「电话性质」一列原样保留，因为在这类清单里「有官网」与「打得通电话」已经脱钩 —— "
    "A 档只代表官网公示，调研方明确未做回拨核验"
)

# 这一份清单的预期形态。数值变了就是数据变了，必须有人看一眼再改。
EXPECTED = {
    "rows": 264,
    "companies": 264,
    "with_website": 151,
    "with_phone": 142,
    "with_address": 260,
    "out_of_province": 14,
}
EXPECTED_TIERS = {"A": 79, "B": 133, "C": 40, "—": 12}


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

    # 归属：46 个产区必须全部落位到已有集群，一行都不许挂空。
    # 注意判据要从**清单侧**查（产区名有没有映射），不能从结果侧查 ——
    # 没落位的产区根本不会出现在 dispatch 表里，从结果侧查永远查不出问题。
    assert len(report["cluster_dispatch"]) == 46, f"产区数应为 46，实际 {len(report['cluster_dispatch'])}"
    assert not report["cluster_unmatched"], f"有行没挂上产区: {report['cluster_unmatched']}"
    # 每个集群都要有企业，否则就是建了个空壳
    assert min(report["cluster_dispatch"].values()) >= 5, report["cluster_dispatch"]
    # 挂到的每一个 slug 都必须真实存在
    known = {
        c["slug"]
        for c in json.loads((ROOT / "data/curated/clusters_seed.json").read_text(encoding="utf-8"))["clusters"]
    }
    unknown = {slug for slug in report["cluster_dispatch"] if slug not in known}
    assert not unknown, f"挂到了不存在的集群: {unknown}"

    # 「对标龙头」不能被算进**本地产区**：这 18 家不是那个镇街的企业。
    # 但它们也**不一定**在省外 —— 天堂伞在萧山、均胜在宁波、森马与人本在温州，
    # 4 家是浙江企业（清单原文写的是「非本地产区」，不是「不在浙江」）。
    # 所以这里断言的是「产区关系」，省外由导入器按企业自己的地址判。
    benchmarks = [r for r in records if "对标" in r["note"] or "非本地" in r["note"]]
    assert len(benchmarks) == 18, len(benchmarks)
    assert all(not r["out_of_province"] for r in benchmarks if r["city"] and r["city"] in ZJ_CITIES), (
        "自己的地址在浙江的对标龙头不该标省外",
        [r["name"] for r in benchmarks if r["out_of_province"] and r["city"] in ZJ_CITIES],
    )
    out_of_province_benchmarks = [r for r in benchmarks if r["out_of_province"]]
    assert len(out_of_province_benchmarks) == 14, len(out_of_province_benchmarks)
    # 它们的行政列说的是本地产区的位置，不能拿来当企业所在地
    assert not any(r["district"] for r in out_of_province_benchmarks if "非本地产区" in "".join(r["raw"].values())), "对标龙头不该带产区县市区"

    # 哨兵值绝不能变成数据
    assert all(not any(s in r["website"] for s in WEBSITE_SENTINELS) for r in records)
    assert all(MOBILE_SHAPE.fullmatch(re.sub(r"\D", "", r["phone"])) is None for r in records), "电话字段里有手机号"
    assert not report["unknown_website_kinds"], report["unknown_website_kinds"]
    assert not report["warnings"], report["warnings"]
    # 「待补（原因）」的原因必须留在备注里，否则页面只剩一片空白
    why = [r for r in records if "电话待补：" in r["note"]]
    assert len(why) >= 5, f"应有多条「电话待补：原因」，实际 {len(why)}"

    if not args.dry_run:
        # 把候选并进自动发现层，否则这批官网不会被联系方式合并读到、
        # 也不会被验收门看见（页面上就是「名单里有官网、显示却未核实」）。
        candidates = [
            {
                "name": r["name"],
                "match_name": r["match_name"],
                "list_names": [LIST_NAME],
                "city": r["city"],
                "district": r["district"],
                "candidate_website": r["website"] or r.get("candidate_website", ""),
                "website": "",
                "public_phone": "",
                "public_email": "",
                "source_url": "",
                "verified_at": "",
                "enrich_status": "pending",
                "discovery_method": "list_candidate",
            }
            for r in records
            if r["website"] or r.get("candidate_website")
        ]
        added, updated = merge_into_discovered(candidates)
        report["discovered_merge"] = {"added": added, "updated": updated}

    # 候选域名必须全部并进了自动发现层：只写 _sites.json 的话，
    # 联系方式合并不读它、验收门也看不见它，等于白导。
    if not args.dry_run:
        merged = json.loads((ROOT / "data/curated/discovered.json").read_text(encoding="utf-8"))
        by_name = {row["name"]: row for row in merged}
        stragglers = [
            r["name"] for r in records
            if (r["website"] or r.get("candidate_website")) and r["name"] not in by_name
        ]
        assert not stragglers, f"有候选域名没并进 discovered.json: {stragglers[:5]}"
        assert all(
            by_name[r["name"]]["enrich_status"] == "verified"
            or by_name[r["name"]].get("candidate_website") == (r["website"] or r.get("candidate_website", ""))
            for r in records
            if r["website"]
        ), "候选域名没有原样保留在 discovered.json 里"


    print(json.dumps({k: v for k, v in report.items() if k not in ("outputs", "entry", "cluster_dispatch")}, ensure_ascii=False, indent=2))
    print(f"\n46 个产区落位（企业数）：")
    for slug, count in sorted(report["cluster_dispatch"].items(), key=lambda kv: -kv[1])[:8]:
        print(f"   {slug:34} {count}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())