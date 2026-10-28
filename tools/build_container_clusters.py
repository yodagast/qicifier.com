"""《全国集装箱移动房企业调研清单》的 46 个产业带 → 调研派生产区档案。

    python tools/build_container_clusters.py [--dry-run]

**为什么需要这一步**：`cluster_slug_index()` 只认得骨架与已派生档案里的名字。
这份清单的「产业带」列有 46 个取值，而库里一条都匹配不上 ——
直接导入的话 **1069 行会全部落空**：企业进得了名录，却不出现在任何集群页上，
而且导入报告不会报错（这是这个库里已经踩过两次的坑）。

**slug 是永久标识，所以写死**。不用 pypinyin 自动转写：转写依赖环境里有没有装
pypinyin、也依赖库的版本，而 slug 会进 URL、进数据库外键。同一个产业带两次生成
出两个 slug，就会出现两条档案、企业被拆到两边。名字 → slug 的对应关系因此
逐条列出，改动时要有人看一眼。

**取值只来自清单自己**：档案名 = 「产业带」列原文 + 档位括注；位置取该产业带下
全部行的「省份 / 地市 / 区县」列，多值就停在上一级。省内外由数据判定 ——
**全部行都是浙江省**的才记为省内产区，其余一律 `out_of_province=true`
（跨省汇总的产业带像「海运集装箱制造」「模块化建筑」本来就横跨十几个省）。

预期形态写成了断言。数值变了就是数据变了，必须有人看一眼再改。
"""

from __future__ import annotations

import argparse
import json
from collections import Counter, OrderedDict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CSV_PATH = ROOT / "docs" / "全国集装箱移动房企业调研清单.csv"
DERIVED = ROOT / "data" / "curated" / "research_clusters.json"

LIST_NAME = "全国集装箱移动房企业调研清单（第三方调研）"

# 「产业带」列原文 → slug。增删产业带时**必须**同步这张表，否则脚本会报错停下
# （而不是静默少建一条档案、让那几十行落空）。
SLUGS: dict[str, str] = {
    # —— 省内（浙江）4 条 ——
    "浙江集装箱房（其他）": "zhejiang-jizhuangfang-qita",
    "宁波集装箱房": "ningbo-jizhuangfang",
    "绍兴集装箱房": "shaoxing-jizhuangfang",
    "杭州集装箱房": "hangzhou-jizhuangfang",
    # —— 省外 42 条 ——
    "西北集装箱房": "xibei-jizhuangfang",
    "江苏集装箱房（其他）": "jiangsu-jizhuangfang-qita",
    "潍坊活动房": "weifang-huodongfang",
    "佛山集装箱房": "foshan-jizhuangfang",
    "河北活动房（其他）": "hebei-huodongfang-qita",
    "海运集装箱制造": "haiyun-jizhuangxiang-zhizao",
    "苏州集装箱房": "suzhou-jizhuangfang",
    "模块化建筑": "mokuaijianzhu",
    "上海集装箱房": "shanghai-jizhuangfang",
    "苏北集装箱房": "subei-jizhuangfang",
    "山东集装箱房（其他）": "shandong-jizhuangfang-qita",
    "广东集装箱房（其他）": "guangdong-jizhuangfang-qita",
    "东莞集装箱房": "dongguan-jizhuangfang",
    "西南集装箱房（其他）": "xinan-jizhuangfang-qita",
    "福建集装箱房（其他）": "fujian-jizhuangfang-qita",
    "东北集装箱房": "dongbei-jizhuangfang",
    "沧州活动房": "cangzhou-huodongfang",
    "青岛集装箱房": "qingdao-jizhuangfang",
    "深圳模块化建筑": "shenzhen-mokuaijianzhu",
    "合肥集装箱房": "hefei-jizhuangfang",
    "成都集装箱房": "chengdu-jizhuangfang",
    "北京集装箱房": "beijing-jizhuangfang",
    "南通集装箱房": "nantong-jizhuangfang",
    "广州集装箱房": "guangzhou-jizhuangfang",
    "江西集装箱房": "jiangxi-jizhuangfang",
    "郑州集装箱房": "zhengzhou-jizhuangfang",
    "临沂活动房": "linyi-huodongfang",
    "泉州集装箱房": "quanzhou-jizhuangfang",
    "徐州活动房": "xuzhou-huodongfang",
    "长沙集装箱房": "changsha-jizhuangfang",
    "天津集装箱房": "tianjin-jizhuangfang",
    "廊坊集装箱房": "langfang-jizhuangfang",
    "厦门集装箱房": "xiamen-jizhuangfang",
    "重庆集装箱房": "chongqing-jizhuangfang",
    "河南集装箱房（其他）": "henan-jizhuangfang-qita",
    "肇庆集装箱房": "zhaoqing-jizhuangfang",
    "衡水活动房": "hengshui-huodongfang",
    "武汉集装箱房": "wuhan-jizhuangfang",
    "常州集装箱房": "changzhou-jizhuangfang",
    "海南集装箱房": "hainan-jizhuangfang",
    "无锡集装箱房": "wuxi-jizhuangfang",
    "湖北集装箱房（其他）": "hubei-jizhuangfang-qita",
}


def category_of(name: str) -> str:
    """产业带名 → 二级行业。取名字里的品类词，不另立一张表。"""
    if "活动房" in name:
        return "活动房"
    if "模块化建筑" in name:
        return "模块化建筑"
    if "海运" in name:
        return "海运集装箱制造"
    return "集装箱房"


def scope_of(rows: list[dict]) -> tuple[str, str, str, bool]:
    """这些行共同的「省 / 市 / 县」与省内外归属。

    多值就停在上一级 —— 只报共识，不猜。**省内外由数据判**：
    全部行都是浙江省才是省内产区；跨省汇总的产业带（「海运集装箱制造」
    「模块化建筑」「西北集装箱房」…）天然横跨多省，只能是省外对比产区。
    """
    provinces = {r["省份"].strip() for r in rows if r["省份"].strip()}
    in_zhejiang = provinces == {"浙江省"}
    province = provinces.pop() if len(provinces) == 1 else ""
    cities = {r["地市"].strip() for r in rows if r["地市"].strip()}
    if len(cities) != 1:
        return province, "", "", in_zhejiang
    city = cities.pop()
    districts = {r["区县"].strip() for r in rows if r["区县"].strip()}
    if len(districts) != 1:
        return province, city, "", in_zhejiang
    return province, city, districts.pop(), in_zhejiang


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="只打印，不写盘")
    args = parser.parse_args()

    rows = list(__import__("csv").DictReader(CSV_PATH.open(encoding="utf-8-sig")))
    assert len(rows) == 1069, f"清单行数应为 1069，实际 {len(rows)}"

    belts: dict[str, list[dict]] = OrderedDict()
    for row in rows:
        belts.setdefault(row["产业带"].strip(), []).append(row)

    # 「产业带」列的取值集合与 SLUGS 必须一一对应，多一个少一个都要停下
    assert set(belts) == set(SLUGS), (
        "产业带与 SLUGS 表不一致：",
        sorted(set(belts) - set(SLUGS)),
        sorted(set(SLUGS) - set(belts)),
    )
    assert len(set(SLUGS.values())) == len(SLUGS), "SLUGS 里有重复的 slug"

    payload = json.loads(DERIVED.read_text(encoding="utf-8"))
    have = {c["slug"] for c in payload["clusters"]}

    added: list[dict] = []
    for name, members in belts.items():
        slug = SLUGS[name]
        province, city, district, in_zhejiang = scope_of(members)
        out_of_province = not in_zhejiang
        if slug in have:
            print(f"  跳过（已存在）：{slug}")
            continue
        kind = "省内调研产区" if not out_of_province else "省外产区，对比参照"
        where = "".join(p for p in (city, district) if p) or province
        added.append(
            {
                "slug": slug,
                "name": f"{name}（{kind}）",
                "city": city,
                "district": district,
                "town": "",
                "industry_l1": "",
                "industry_l2": category_of(name),
                "core_area": f"{where}（{name}）",
                "scale_tier": kind,
                "tier": "C",
                "data_grade": "site_curated",
                "out_of_province": out_of_province,
                "metric_set": [],
                "honors": [],
                "note": (
                    f"{kind}：本条档案由《全国集装箱移动房企业调研清单》派生，覆盖该清单"
                    f"「产业带」列取值「{name}」的 {len(members)} 行。产区归属由清单自带，"
                    "不靠按产品推断。"
                    + (
                        "省外产区，本库仅作对比参照，不参与浙江产区统计。"
                        if out_of_province
                        else "规模与荣誉暂无带口径数字，因此不列指标。"
                    )
                ),
                "publish_rule": "站方整理",
                "source_name": LIST_NAME,
                "source_grade": "site_curated",
            }
        )

    zj = [r for r in added if not r["out_of_province"]]
    print(f"将新增 {len(added)} 条派生档案（省内 {len(zj)} / 省外 {len(added) - len(zj)}）；现有 {len(payload['clusters'])} 条")
    for row in added:
        print(
            f"  {row['slug']:30s} {row['name']:32s} "
            f"{row['city'] or '—':8s}{row['district'] or '—':10s} oop={row['out_of_province']}"
        )
    if args.dry_run:
        return 0

    payload["clusters"].extend(added)
    DERIVED.write_text(
        json.dumps(payload, ensure_ascii=False, indent=1) + "\n", encoding="utf-8"
    )
    print(f"已写入 {DERIVED.relative_to(ROOT)}，共 {len(payload['clusters'])} 条")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
