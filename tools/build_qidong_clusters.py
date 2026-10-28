"""《全国气动元件企业调研清单》的 30 个产业带 → 调研派生产区档案。

    python tools/build_qidong_clusters.py [--dry-run]

**为什么需要这一步**：`cluster_slug_index()` 只认得骨架与已派生档案里的名字，
所以清单导入时「产业带」列认不出来的行会**静默地不出现在任何集群页上**
（不是报错，是那几十行看着导进去了却查不到）。上一份《浙江省产业带企业调研清单》
就是这么丢掉 355 行的——见 README「名单批量导入」那一节。

**取值只来自清单自己**：档案名 = 清单「产业带」列的原文 + 我们加的档位括注，
位置 = 该产业带下全部行的「省份/地市/区县」列（多值就停在上一级）。
不另立一张「产业带 → 位置」的表 —— 表会与清单漂移，而漂移是静默的。

**两类产业带的处理不同**：

* 逐行的产区（27 条）：一条产业带一条档案，级别与骨架里 91 个镇街产区一致。
* 跨产区的企业池（3 条）：「国产气动龙头」与「外资品牌在华」是选出来的**龙头企业**，
  它们的所在城市就是各自产区（奉化/乐清/宁波…），把它们各自挂一条产区档案，
  会让同一家企业在两个产区里各算一次。所以这两条**不建档案、也不挂集群**，
  企业仍以 `list_names` 记录「来自哪份名单的哪一类」，产区归属落在它自己的城市上。
* 「奉化气动元件」已经有骨架档案 `fenghua-qidong`（镇街级块状经济），
  指向它而不是新建 —— 同一个产区两条档案会让企业被拆到两边、页面各显示一半。

预期形态写成了断言。数值变了就是数据变了，必须有人看一眼再改。
"""

from __future__ import annotations

import argparse
import csv
import json
from collections import Counter, OrderedDict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CSV_PATH = ROOT / "docs" / "全国气动元件企业调研清单.csv"
DERIVED = ROOT / "data" / "curated" / "research_clusters.json"

LIST_NAME = "全国气动元件企业调研清单（第三方调研）"

# 产业带名 → slug。**名字来自清单原文，只有 slug 是我们起的。**
#
# 「奉化气动元件」不在这里：骨架里已经有 `fenghua-qidong`（镇街级块状经济，
# 带 town=锦屏街道、scale_tier=块状经济），`cluster_slug_index()` 直接就能解析它。
# 而 `research_clusters.json` 在灌库时是**后加载、覆盖骨架**的（见 loader 的两段
# 同名 slug 赋值），在这里再声明一遍会把那条档案的镇街与档位改掉 —— 那是回归。
# 所以它只需在下面被断言为「已能解析」，不要生成。
IN_PROVINCE = {
    "宁波气动元件": "ningbo-qidong-yuanjian",
    "乐清气动元件": "yueqing-qidong-yuanjian",
    "温州气动元件": "wenzhou-qidong-yuanjian",
    "温岭气动工具": "wenling-qidong-gongju",
    "台州气动元件": "taizhou-qidong-yuanjian",
    "浙江气动元件（其他）": "zhejiang-qidong-qita",
}
OUT_OF_PROVINCE = {
    "上海气动元件": "shanghai-qidong",
    "无锡气动元件": "wuxi-qidong",
    "常州气动元件": "changzhou-qidong",
    "苏州气动元件": "suzhou-qidong",
    "江苏气动元件（其他）": "jiangsu-qidong-qita",
    "东莞气动元件": "dongguan-qidong",
    "深圳气动元件": "shenzhen-qidong",
    "广东气动元件（其他）": "guangdong-qidong-qita",
    "山东气动元件": "shandong-qidong",
    "福建气动元件": "fujian-qidong",
    "河北气动元件": "hebei-qidong",
    "天津气动元件": "tianjin-qidong",
    "重庆气动元件": "chongqing-qidong",
    "河南气动元件": "henan-qidong",
    "四川气动元件": "sichuan-qidong",
    "湖北气动元件": "hubei-qidong",
    "湖南气动元件": "hunan-qidong",
    "安徽气动元件": "anhui-qidong",
    "辽宁气动元件": "liaoning-qidong",
    "吉林气动元件": "jilin-qidong",
    "陕西气动元件": "shaanxi-qidong",
}
# 跨产区的企业池：不建档案、不挂集群（理由见模块开头）
POOLS = ("国产气动龙头", "外资品牌在华")


def scope_of(rows: list[dict]) -> tuple[str, str, str, str]:
    """这些行共同的「省 / 市 / 县 / 镇」。多值就停在上一级 —— 只报共识，不猜。

    市或县出现多个取值，是「XX气动元件（其他）」这类**跨市汇总**产业带的正常形态
    （清单把它们打包成一条），不是数据有问题。所以它停在能确定的那一级：
    省 + 市的共识 → 报省市；连市都不一致 → 只报省。
    """
    provinces = {r["省份"].strip() for r in rows}
    province = provinces.pop() if len(provinces) == 1 else ""
    cities = {r["地市"].strip() for r in rows if r["地市"].strip()}
    if len(cities) != 1:
        return province, "", "", ""
    city = cities.pop()
    districts = {r["区县"].strip() for r in rows if r["区县"].strip()}
    if len(districts) != 1:
        return province, city, "", ""
    return province, city, districts.pop(), ""


ALREADY_RESOLVED = {"奉化气动元件": "fenghua-qidong"}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="只打印，不写盘")
    args = parser.parse_args()

    rows = list(csv.DictReader(CSV_PATH.open(encoding="utf-8-sig")))
    assert len(rows) == 460, f"清单行数应为 460，实际 {len(rows)}"

    belt: dict[str, list[dict]] = OrderedDict()
    for row in rows:
        belt.setdefault(row["产业带"].strip(), []).append(row)

    unknown = set(belt) - set(IN_PROVINCE) - set(OUT_OF_PROVINCE) - set(POOLS) - set(ALREADY_RESOLVED)
    assert not unknown, f"清单里出现了没归类的产业带：{unknown}"

    # 指向已有骨架档案的那几条必须**真的能解析到**，否则它们会静默落空
    import sys

    sys.path.insert(0, str(ROOT / "backend"))
    from app.services.list_import import cluster_slug_index  # noqa: E402

    index = cluster_slug_index()
    for name, slug in ALREADY_RESOLVED.items():
        assert index.get(name) == slug, (
            f"「{name}」本应解析到已有档案 {slug}，实际 {index.get(name)!r}"
        )

    # 「XX（其他）」是**跨市汇总**产业带，产地本来就到不了市一级；其余的必须能定位，
    # 定位不了说明清单的结构变了（比如把某个市的行拆走了），要有人看一眼。
    for name, members in belt.items():
        if name in POOLS:
            continue
        province, _city, _district, _town = scope_of(members)
        # 省内产区必须**定位到市**：唯一的例外是「浙江气动元件（其他）」这条跨市汇总
        # （清单自己把它打包成一条），其余 6 条各自对应一个市，定位不到就说明清单结构变了。
        if name in OUT_OF_PROVINCE:
            # 省外产区只要求**省份唯一**：山东/福建这些是全省口径的产业带，
            # 行分布在好几个市是正常形态（与省内「（其他）」同理）。
            assert province and province != "浙江省", f"「{name}」自称省外却是浙江的"
        else:
            assert province == "浙江省", f"「{name}」在浙江产区名单里，省份却是 {province!r}"
            if not name.endswith("（其他）"):
                _p, city, _d, _t = scope_of(members)
                assert city, (
                    f"「{name}」的行分布在不同地市，档案写不下一个产地："
                    f"{Counter(r['地市'] for r in members)}"
                )

    payload = json.loads(DERIVED.read_text(encoding="utf-8"))
    have = {c["slug"] for c in payload["clusters"]}
    added: list[dict] = []
    for name, slug in list(IN_PROVINCE.items()) + list(OUT_OF_PROVINCE.items()):
        members = belt[name]
        out_of_province = name in OUT_OF_PROVINCE
        province, city, district, town = scope_of(members)
        if slug in have:
            print(f"  跳过（已存在）：{slug}")
            continue
        kind = "省外产区，对比参照" if out_of_province else "省内调研产区"
        where = "".join(p for p in (city, district) if p) or province or "浙江省"
        added.append(
            {
                "slug": slug,
                "name": f"{name}（{kind}）",
                "city": city,
                "district": district,
                "town": town,
                "industry_l1": "",
                "industry_l2": "气动元件",
                "core_area": f"{where}（{name}）",
                "scale_tier": kind,
                "tier": "C",
                "data_grade": "site_curated",
                "out_of_province": out_of_province,
                "metric_set": [],
                "honors": [],
                "note": (
                    f"{kind}：本条档案由《全国气动元件企业调研清单》派生，覆盖该清单"
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

    print(f"将新增 {len(added)} 条派生档案（现有 {len(payload['clusters'])} 条）")
    for row in added:
        print(
            f"  {row['slug']:30s} {row['name']:32s} "
            f"{row['city'] or '—'}/{row['district'] or '—'} oop={row['out_of_province']}"
        )
    print(f"不建档案的企业池：{'、'.join(POOLS)}（{sum(len(belt[p]) for p in POOLS)} 家）")
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
