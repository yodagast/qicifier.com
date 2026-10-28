"""工具链回归测试：官网验收规则、联系方式摘要抽取。

**不需要网络**：抓取结果用合成页面 / 合成摘要代替，断言的只是判定逻辑本身
（哪些能过门、哪些必须被拒）。真实页面的证据见 README「验收规则的四次迭代」。

    python tests/test_tools.py

为什么这些断言值得单独守：验收规则每放宽一次，都可能把「提到过这家企业」
的页面放进来（v1、v2 都栽在这上面），而这类错误会直接污染公开页上的
「官方认定」标签。规则里的每个分支都对应一次真实误报或漏报，
所以它们必须以测试形式固定住，而不是靠文档里的例子。
"""

from __future__ import annotations

import json
import csv
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "backend"))

import directory_contacts as dc  # noqa: E402
import discover_sites as ds  # noqa: E402

COMPANY = "杭州测测测科技有限公司"

passed = failed = 0
failures: list[str] = []


def check(name: str, ok: bool, detail: object = "") -> None:
    global passed, failed
    if ok:
        passed += 1
    else:
        failed += 1
        failures.append(f"{name} — {detail!r}")


def page(title: str, body: str, copyright_line: str = "") -> str:
    return (
        f"<html><head><title>{title}</title></head><body><div class='nav'>首页 产品 联系</div>"
        f"<div class='main'>{body}</div>"
        f"<div class='footer'>{copyright_line}</div></body></html>"
    )


def main() -> int:
    print("=== 官网验收规则 ===")
    check("验收规则版本为 4", ds.RULE_VERSION == 4, ds.RULE_VERSION)
    check(
        "当前规则放宽后不再跨版本复用否定结论",
        ds.NEGATIVE_REUSE_VERSIONS == frozenset(),
        ds.NEGATIVE_REUSE_VERSIONS,
    )

    hair = "杭州惠耳听力技术设备有限公司"
    # 真站：标题和版权行只有品牌，全称只出现在「公司简介」的自我介绍句里
    huier = page(
        "助听器品牌,助听器哪家好--惠耳听力",
        f"<p>{hair}是一家专业从事助听器研发、生产、验配与服务的高新技术企业。</p>",
        "Copyright &copy; All Rights Reserved 惠耳听力 版权所有 浙ICP备08000334号-1",
    )
    check("品牌站：全称出现在正文自我介绍句里 → 收录", ds.accepted(huier, "助听器品牌,助听器哪家好--惠耳听力", hair) == "body-selfid")

    # 兄弟公司站：全称只出现在地址行，且标题声明了另一家公司
    xinduoda = page(
        "杭州信多达电器有限公司",
        f"<p>地址：杭州市大江东产业聚集区经六路2977号，{hair} 联系电话：0571-57575888</p>",
        "Copyright ? 2018 信多达 All Rights Reserved",
    )
    check(
        "兄弟公司站：全称只在地址行出现 → 拒绝",
        ds.accepted(xinduoda, "杭州信多达电器有限公司", hair) == "",
        ds.accepted(xinduoda, "杭州信多达电器有限公司", hair),
    )

    # 同一段自我介绍句，但标题声明了另一家公司 → 仍然拒绝
    hijack = page(
        "杭州信多达电器有限公司",
        f"<p>{hair}是一家专业从事智能装备的高新技术企业。</p>",
    )
    check(
        "自我介绍句 + 标题是别家公司 → 拒绝",
        ds.accepted(hijack, "杭州信多达电器有限公司", hair) == "",
        ds.accepted(hijack, "杭州信多达电器有限公司", hair),
    )

    # 集团站：从未以自我介绍句提到子公司
    huafeng = page(
        "华峰集团",
        "<p>华峰化学股份有限公司为集团旗下上市公司，主要从事氨纶业务。</p>",
        "版权所有 华峰集团有限公司 浙ICP备05012345号",
    )
    check(
        "集团站：子公司只是被提及 → 拒绝",
        ds.accepted(huafeng, "华峰集团", "华峰化学股份有限公司") == "",
        ds.accepted(huafeng, "华峰集团", "华峰化学股份有限公司"),
    )

    # 全称在标题（v3 路径必须保留）
    znmeter = page("中南仪表有限公司-电能计量产品生产商", "<p>主营电能表</p>")
    check("全称在标题 → 收录", ds.accepted(znmeter, "中南仪表有限公司-电能计量产品生产商", "中南仪表有限公司") == "title")

    # 全称在版权行（v3 路径必须保留）
    jsjd = page("晶盛机电", "<p>晶体生长设备</p>", "版权所有 浙江晶盛机电股份有限公司 浙ICP备11012345号")
    check("全称在版权行 → 收录", ds.accepted(jsjd, "晶盛机电", "浙江晶盛机电股份有限公司") == "copyright")

    # script 里的全称不算（UUID/埋点数据常放这里）
    scripted = page("某某平台", f"<script>var company='{hair}';</script>", "版权所有 某某平台")
    check("全称只出现在 script 里 → 拒绝", ds.accepted(scripted, "某某平台", hair) == "")

    print("\n=== 候选并发校验的顺序不变式 ===")
    original = ds.verify
    calls: list[str] = []

    def fake_verify(url: str, name: str, timeout: int = 14):
        calls.append(url)
        return {"url": url} if "second" in url else None

    try:
        ds.verify = fake_verify
        hit = ds.verify_first(["https://a.example/", "https://second.example/", "https://c.example/"], "测试")
        check("并发后仍按候选优先级返回第一个命中", hit is not None and "second" in hit[0], hit)
        check("所有候选都被校验（并发不改变覆盖面）", len(calls) == 3, calls)
        check("没有命中时返回 None", ds.verify_first(["https://x.example/"], "测试") is None)
    finally:
        ds.verify = original

    print("\n=== 抓取失败必须区分「不通过」和「抓不到」 ===")
    original_get = ds.http_get

    def fake_get(url: str, timeout: int = 15, max_bytes: int = 400_000):
        if "down" in url:
            raise OSError("connection refused")
        if "nocontent" in url:
            return 204, url, ""
        if "otherco" in url:
            return 200, url, page("某某电气有限公司", "<p>与本目录无关的页面</p>")
        return 200, url, page(f"{COMPANY}-首页", f"<p>{COMPANY}是一家专业从事测试的企业。</p>")

    try:
        ds.http_get = fake_get
        status, meta = ds.classify("https://ok.example/", COMPANY, timeout=3)
        check("能证明归属 → accepted", status == "accepted" and meta is not None, status)
        status, meta = ds.classify("https://otherco.example/", COMPANY, timeout=3)
        check("页面取到了但不能证明归属 → rejected", status == "rejected" and meta is None, status)
        status, _ = ds.classify("https://down.example/", COMPANY, timeout=3)
        check("连接失败 → unreachable", status == "unreachable", status)
        status, _ = ds.classify("https://nocontent.example/", COMPANY, timeout=3)
        check("204 无正文 → unreachable", status == "unreachable", status)
    finally:
        ds.http_get = original_get

    print("\n=== 复验：抓不到不降级，误收才降级 ===")
    original_classify = ds.classify

    def fake_classify(url: str, name: str, timeout: int = 14):
        if "keep" in url:
            return "accepted", {"url": url, "checked_at": "2026-01-01T00:00:00+08:00", "title": "t"}
        if "down" in url:
            return "unreachable", None
        return "rejected", None

    try:
        ds.classify = fake_classify
        checked = {
            "保留": {"enrich_status": "verified", "website": "https://keep.example/"},
            "不可达": {"enrich_status": "verified", "website": "https://down.example/"},
            "误收": {"enrich_status": "verified", "website": "https://bad.example/"},
            "无网址": {"enrich_status": "verified"},
        }
        kept, demoted = ds.revalidate(checked)
        check("复验统计正确", (kept, demoted) == (1, 2), (kept, demoted))
        check("通过 → 仍是 verified", checked["保留"]["enrich_status"] == "verified")
        check(
            "站点不可达 → 保留原结论，只标待复检",
            checked["不可达"]["enrich_status"] == "verified" and checked["不可达"].get("recheck_needed") is True,
            checked["不可达"],
        )
        check("页面取到了但不再证明归属 → 降级", checked["误收"]["enrich_status"] == "failed_revalidation")
        check("没有网址 → 回到查不到", checked["无网址"]["enrich_status"] == "no_site_found")
        stale = {"enrich_status": "verified", "website": "https://keep.example/", "recheck_needed": True}
        ds.revalidate({"保留": stale})
        check("复验通过后清掉上次的待复检标记", "recheck_needed" not in stale, stale)
    finally:
        ds.classify = original_classify

    print("\n=== 名单导入：官方名单与第三方调研清单不能混为一谈 ===")
    import companies as cli_companies  # noqa: E402
    from app.seed.companies import load_company_skeletons  # noqa: E402

    cli_set = cli_companies.load_companies()
    backend_set = load_company_skeletons()
    check("两份骨架实现的公司数一致", len(cli_set) == len(backend_set) == 4228, (len(cli_set), len(backend_set)))
    # 两份骨架实现必须给出同一条记录。唯一允许的差异是 `cluster_slug`：
    # 归属映射只有入库时才用得到（见 LIST_CLUSTER 的注释），它不能反过来影响
    # 「哪些企业存在」这个判断。除此之外任何字段差异都是漂移。
    # `cluster_slugs`（集群归属）只有入库时才用得到，CLI 侧不需要计算它，
    # 所以它的**值**允许不同；除此之外任何差异都是漂移。
    backend_only_values = {"cluster_slugs"}
    cli_keys = {k for c in cli_set for k in c}
    backend_keys = {k for c in backend_set for k in c}
    check("两份骨架实现的字段集合一致", cli_keys == backend_keys, cli_keys ^ backend_keys)
    stripped = lambda rows: [  # noqa: E731
        {k: v for k, v in c.items() if k not in backend_only_values} for c in rows
    ]
    check(
        "两份骨架实现除集群归属外完全相同",
        stripped(cli_set) == stripped(backend_set),
        "tools/companies.py 与 seed/companies.py 已漂移",
    )
    check(
        "集群归属只在后端计算（CLI 侧为空）",
        all(not c["cluster_slugs"] for c in cli_set)
        and any(c["cluster_slugs"] for c in backend_set),
    )

    grades = {c["name"]: c["data_grade"] for c in backend_set}
    from collections import Counter

    check(
        # 三档：616 官方名单 + 3028 第三方调研 + 584 政府公开（临安规上企业底表）
        "核实程度按名单分级（616 官方 + 3028 调研 + 584 政府公开）",
        Counter(grades.values()) == {"official": 616, "site_curated": 3028, "gov_open": 584},
        Counter(grades.values()),
    )

    research = [c for c in backend_set if c["data_grade"] == "site_curated"]
    check("调研清单企业全部带核实档位", all(c["verify_tier"] for c in research))
    check(
        # 137 行修订版（A19/B114/C4）并入后：A253+19-4 重合的 A 档…
        # 口径是「合并后的企业」，不是「行数之和」—— 首版独有的 3 家仍在（含 C 档 2 家）。
        # 八份调研清单合计（第七份 = 浙江省415X先进制造业企业调研清单 779 行，
        # 其中 612 家已在其余名单里，档位归先到的那份；它自己带进 A87/B76/C4）。
        # 医疗器械那份自己 309 行是 A195/B112/C2，
        # 其中有 30 家已在别的清单里（档位归先到的那份），所以并进来只涨 279 家。
        # 第九份（气动元件）带进 A66/B302/C92、第十份（集装箱移动房）带进 A98/B692/C279，
        # 已在库的按先到的档位算
        "十份调研清单合计档位分布正确（A807 / B1716 / C497 / 未标 8）",
        Counter(c["verify_tier"][0] for c in research) == {"A": 807, "B": 1716, "C": 497, "—": 8},
        Counter(c["verify_tier"][0] for c in research),
    )
    # 不是每份清单都有「主营产品」列：镇街级那份只有产区级的「产业」标签，
    # 所以这条断言只覆盖**确实带产品列的**清单（全国代表、武义）。
    # 硬性要求所有清单都有产品，只会逼着后来的人去编一个字段。
    #
    # 无缝服装那份也**不在**这条里：它是底表型清单（工商经营范围 + 官网自述），
    # 137 行里一行都没有「主营产品」列。要它有产品，只能把备注里那句
    # 「品类待确认」编成一个产品名 —— 那正是这条注释当初想避免的事。
    # 现代纺织与服装那份同理：它的品类信息在「产业」列（现代纺织·化纤…），
    # 那是**产业标签**不是产品名，硬塞进 products 就是编字段。医疗器械那份的
    # 「产业」列（医疗器械·体外诊断）也一样 —— 所以同样排除。
    with_products = [
        c for c in research
        if "全国代表" not in " ".join(c["list_names"])
        and "镇街级" not in " ".join(c["list_names"])
        and "无缝服装" not in " ".join(c["list_names"])
        and "现代纺织与服装" not in " ".join(c["list_names"])
        and "医疗器械" not in " ".join(c["list_names"])
        # 415X 调研清单只有「赛道 / 产业」标签，没有「主营产品」列 ——
        # 与镇街级那份同理，硬塞进 products 就是编字段。
        and "415X" not in " ".join(c["list_names"])
        # 气动元件清单同理：品类在「产业带」列（「奉化气动元件」），
        # 没有逐行的产品名 —— 它有「备注」列写产品，但那是备注不是产品列。
        and "气动元件" not in " ".join(c["list_names"])
    ]
    check(
        "带产品列的调研清单企业全部有主营产品",
        all(c["products"] for c in with_products),
        [c["name"] for c in with_products if not c["products"]][:3],
    )
    # 「无缝服装」那份的 7 行「其他产区」是**作者自己写的「别处」**（揭阳普宁、
    # 湖州、东阳、海宁、诸暨、昆山各一家），不是我们没认出来。不挂集群是刻意的：
    # 挂错产区比不挂更难发现。所以要求「全部能落到集群」时要把这 7 家排除掉，
    # 但**必须**钉住它们的数量 —— 否则哪天「其他产区」变成 70 家也没人会注意。
    seamless = [c for c in research if "无缝服装" in " ".join(c["list_names"])]
    # 415X 调研清单是**唯一**允许大面积不挂集群的调研清单：它的「赛道」是
    # 省级产业分类，其中 8 个赛道（中医药大健康 / 光电 / 生物美妆 / 算力产业…）
    # 只对应按县命名的「新星」产业群，企业不在那个县就不该挂过去。
    # 单独钉住它的数量，而不是把它混进下面这条「除其他产区外全部落位」。
    new_list_name = "浙江省415X先进制造业企业调研清单（第三方调研）"
    pneumatic_name = "全国气动元件企业调研清单（第三方调研）"
    research_legacy = [
        c
        for c in research
        if new_list_name not in " ".join(c["list_names"])
        and pneumatic_name not in " ".join(c["list_names"])
    ]
    loose = [c for c in research_legacy if not c["cluster_slugs"]]
    check(
        "调研清单企业除「其他产区」外全部能落到集群",
        len(loose) == 7
        and all("其他产区" in c["note"] or c["name"] in {x["name"] for x in loose} for c in loose)
        and all(c["cluster_slugs"] for c in research_legacy if c not in loose),
        [c["name"] for c in loose][:8],
    )
    # 气动元件清单：460 家全部入库，其中 417 家有集群归属（28 条「产业带」逐条建档，
    # 每条的行数都对得上），43 家**刻意不挂**——它们来自两条跨产区的企业池
    # （「国产气动龙头」22 家 + 「外资品牌在华」21 家），所在城市就是各自的产区，
    # 挂到某一条气动产业带下会让同一家企业在两个产区里各算一次。
    # 单独钉住这三个数字：少了说明某条产业带没建档（几十行会静默落空），
    # 多了说明有人给企业池也建了档案。
    pneumatic = [c for c in research if pneumatic_name in " ".join(c["list_names"])]
    pneumatic_loose = [c for c in pneumatic if not c["cluster_slugs"]]
    check(
        "气动元件清单 460 家、417 家有产区归属、43 家属跨产区企业池（刻意无归属）",
        len(pneumatic) == 460
        and len(pneumatic_loose) == 43
        and len(pneumatic) - len(pneumatic_loose) == 417,
        (len(pneumatic), len(pneumatic_loose), [c["name"] for c in pneumatic_loose][:3]),
    )
    # 415X 清单：779 家全部入库，其中 708 家有集群归属（415X 赛道按名直挂、
    # 「新星」产业群按县落位），71 家确实无集群可挂（赛道只对应外地产业群）。
    # 那个数变了就该有人看一眼：它意味着「赛道列」的落位规则失效了。
    from_415x = [c for c in backend_set if new_list_name in " ".join(c["list_names"])]
    check(
        "415X 调研清单 779 家、其中 71 家无集群归属（赛道只对应外地新星产业群）",
        len(from_415x) == 779 and sum(1 for c in from_415x if not c["cluster_slugs"]) == 71,
        (len(from_415x), sum(1 for c in from_415x if not c["cluster_slugs"])),
    )
    check(
        "无缝服装清单 145 家（修订版 137 + 首版独有 3 + 官方名单重合的公司名更多）",
        len(seamless) >= 140,
        len(seamless),
    )

    check(
        "地区来源按名单分级，省外单独标记",
        {c["region_source"] for c in research} == {"research_list", "out_of_province", "benchmark"},
        {c["region_source"] for c in research},
    )
    check(
        # 无缝对比产区 33 + 全国代表 52 + 镇街级「对标龙头」里的省外 14 + 首版独有 3
        # + 现代纺织与服装清单的省外 167（172 行里 5 家早在库且已是省外）
        # + 医疗器械清单的省外 176（其中 1 家早在库且已是省外）
        # + 415X 调研清单的省外 2
        # + 全国气动元件清单的省外 231（460 行里 231 行省份不是浙江；其中 29 家
        #   属「国产气动龙头 / 外资品牌在华」两条企业池，它们也不参与浙江统计）
        # + 全国集装箱移动房清单的省外 917（1069 行里 917 行的地市不是浙江的市）
        # + 浙江省产业带调研清单的省外 4 —— 比上一版少 3 家：舟山渔业那 3 行
        #   （中国水产舟山海洋渔业 / 舟山海洲水产 / 舟山震洋）的行政列写的是
        #   对标的「上海市普陀区」，而地址里的「普陀区沈家门街道」其实是舟山的
        #   （浙江与外省重名的那一坑）。现在按企业自己的地址改判回浙江。
        # `out_of_province` 优先于 `benchmark`（两件事都真时按省域记），
        # 所以 JUKI / 新秀丽 / Herman Miller / 鄂尔多斯 记在省外那一档里。
        "省外（对比产区 + 全国代表 + 对标龙头 + 纺织 + 医疗器械 + 415X + 产业带 + 气动 + 集装箱房）共 1594 家",
        sum(1 for c in research if c["region_source"] == "out_of_province") == 1594,
        sum(1 for c in research if c["region_source"] == "out_of_province"),
    )
    check(
        # 「对标龙头」是产区关系，不是省域关系：18 家里 4 家是浙江企业
        # （天堂伞在萧山、均胜在宁波、森马与人本在温州），它们单独记一类。
        "对标龙头里在浙江的 4 家与省外 14 家分开记",
        sum(1 for c in research if c["region_source"] == "benchmark") == 4
        and {
            c["name"] for c in research if c["region_source"] == "benchmark"
        } == {
            "杭州天堂伞业集团有限公司",
            "宁波均胜电子股份有限公司",
            "巴拉巴拉（浙江森马服饰股份有限公司）",
            "人本集团有限公司",
        },
        [c["name"] for c in research if c["region_source"] == "benchmark"],
    )
    check(
        "代工型/品牌型业态已标注（调研方案要求分开两类实体）",
        {"代工型（OEM/ODM 为主）", "品牌型（面向国内消费者）", "品类专精型（男士内裤）"}
        <= {c["business_model"] for c in research},
        {c["business_model"] for c in research},
    )

    # 哨兵值绝不能变成数据：CSV 里「未获取」「官网为占位符」占了绝大多数行，
    # 一旦漏进 website/phone 列，页面上就会出现一堆假官网。
    sentinels = ("未获取", "无官网", "占位符", "0000-00000000")
    raw_rows = json.loads((ROOT / "data" / "source" / "wuyi_outdoor_2026.json").read_text(encoding="utf-8"))
    check("解析结果里没有任何哨兵值", not any(s in r["website"] or s in r["phone"] for r in raw_rows for s in sentinels))
    check(
        "可用官网/电话数量与断言一致",
        (sum(1 for r in raw_rows if r["website"]), sum(1 for r in raw_rows if r["phone"])) == (9, 6),
        (sum(1 for r in raw_rows if r["website"]), sum(1 for r in raw_rows if r["phone"])),
    )
    check(
        "手机号没有被当成企业电话收进来",
        not any(re.fullmatch(r"1[3-9]\d{9}", re.sub(r"\D", "", r["phone"])) for r in raw_rows if r["phone"]),
    )

    # 联系方式层的标签必须逐行降级
    layer = json.loads((ROOT / "data" / "curated" / "research_list.json").read_text(encoding="utf-8"))
    labels = {r["verify_tier"]: r["source_label"] for r in layer}
    check("A 档标注为官网公示（未复核）", "官网公示" in labels.get("A 官网公示", ""), labels.get("A 官网公示"))
    check("C 档标注为未核线索", "未核" in labels.get("C 线索（未核）", ""), labels.get("C 线索（未核）"))
    check(
        "A 档与 C 档的标签不同（不能共用一层标签）",
        labels.get("A 官网公示") != labels.get("C 线索（未核）"),
        labels,
    )
    check("联系方式层每行都带来源标签", all(r.get("source_label") for r in layer))

    print("\n=== 全国无缝服装清单：哨兵、地址拆分、手机号 ===")
    seam = json.loads((ROOT / "data" / "source" / "seamless_garment_2026.json").read_text(encoding="utf-8"))
    check("行数为 19", len(seam) == 19, len(seam))
    check(
        "档位分布与清单一致（A4 / B6 / C9）",
        Counter(r["verify_tier"][0] for r in seam) == {"A": 4, "B": 6, "C": 9},
        Counter(r["verify_tier"][0] for r in seam),
    )
    check(
        "哨兵值未进入官网/电话列",
        not any(s in r["website"] or s in r["phone"] for r in seam for s in ("未获取", "官网未公示", "占位符")),
    )
    check(
        "手机号没有被当成企业电话收进来",
        not any(re.fullmatch(r"1[3-9]\d{9}", re.sub(r"\D", "", r["phone"])) for r in seam if r["phone"]),
        [r["name"] for r in seam if r["phone"]],
    )
    check(
        "官网公示手机号的企业在备注里写明未收录原因",
        sum(1 for r in seam if "电话未收录" in r["note"] and "手机" in r["note"]) == 2,
        [r["name"] for r in seam if "电话未收录" in r["note"]],
    )
    # 规则二：地址拆成展示地址 + 注册地，冲突写进说明而不是塞进地址字段
    duel = [r for r in seam if r["address_registered"]]
    check("地址两处来源不同的企业拆出了注册地", len(duel) == 1 and "佛堂镇" in duel[0]["address_registered"], duel)
    check(
        "两处地址不一致的企业没有把两个地址塞进同一个字段",
        all("／" not in r["address"] and "不一致" not in r["address"] for r in seam),
        [r["address"] for r in seam if "／" in r["address"]],
    )
    check("省外产区正好 8 家", sum(1 for r in seam if r["out_of_province"]) == 8)
    check(
        "省外企业的行政区划落在省外，不会被塞进浙江的县",
        {r["district"] for r in seam if r["out_of_province"]} == {"潮阳区", "南海区", "晋江市"},
        {r["district"] for r in seam if r["out_of_province"]},
    )

    print("\n=== 第四批产业集群「全国代表企业」：挂到已有集群，不新建集群 ===")
    reps = json.loads((ROOT / "data" / "source" / "cluster_reps.json").read_text(encoding="utf-8"))
    # 387 = 第一批 161 + 第二批 226（副本把两批合在一起；第一批与原文件逐字段一致）
    check("行数为 387（第一批 161 + 第二批 226）", len(reps) == 387, len(reps))
    check(
        "两批的批次列都保留下来了",
        Counter(r.get("batch") for r in reps) == {"第一批": 161, "第二批": 226},
        Counter(r.get("batch") for r in reps),
    )
    check(
        "档位分布与清单一致（A180 / B184 / C23）",
        Counter(r["verify_tier"][0] for r in reps) == {"A": 180, "B": 184, "C": 23},
        Counter(r["verify_tier"][0] for r in reps),
    )
    check(
        "「官网类型」没有未识别的取值（不认识的类型一律不发布）",
        all(r["website_kind"] in {
            "企业官网", "科研机构官网", "品牌站", "品牌海外站", "集团官网",
            "母站企业官网", "母公司官网", "第三方商铺", "第三方／集团站", "",
        } for r in reps),
        Counter(r["website_kind"] for r in reps),
    )
    check(
        "「待核」的裸域名不发布为官网，只作候选",
        all(
            r["website"] == "" and r["candidate_website"] and "仅作候选" in r["note"]
            for r in reps
            if r["website_kind"] == "" and re.fullmatch(r"[a-z0-9.-]+\.[a-z]{2,}", r["raw"]["website"], re.I)
        ),
        [r["name"] for r in reps if r.get("candidate_website")],
    )
    check(
        "哨兵值里夹带的线索写进备注，但不揪出来当官网",
        all(r["website"] == "" and "清单给的线索：杭钢集团 hzsteel.com" in r["note"]
            for r in reps if "hzsteel.com" in r["raw"]["website"]),
        [r["name"] for r in reps if "hzsteel.com" in r["raw"]["website"]],
    )
    # 「官网类型」决定这个链接能不能当官网：第三方商铺填进官网字段，
    # 采购商点进去看到的是中间平台而不是工厂。
    check(
        "第三方商铺页没有进官网字段",
        all(r["website"] == "" for r in reps if "商铺" in r["website_kind"]),
        [r["name"] for r in reps if r["website"] and "商铺" in r["website_kind"]],
    )
    check(
        "集团/母公司官网如实记下链接类型",
        sum(1 for r in reps if r["website_kind"] in ("集团官网", "母站企业官网", "母公司官网")) == 26
        and all(r["website"] for r in reps if r["website_kind"] in ("集团官网", "母站企业官网", "母公司官网")),
        Counter(r["website_kind"] for r in reps),
    )
    check(
        "官网类型为「无独立官网」的企业在备注里说明",
        all("官网类型" in r["note"] for r in reps if r["website_kind"] == "第三方商铺" and "待补" not in r["raw"]["website"]),
        [r["name"] for r in reps if r["website_kind"] == "第三方商铺"],
    )
    check(
        "多号码写法取第一个合法座机，手机号不收录",
        sum(1 for r in reps if r["phone"]) == 239
        and not any(re.fullmatch(r"1[3-9]\d{9}", re.sub(r"\D", "", r["phone"])) for r in reps if r["phone"]),
        sum(1 for r in reps if r["phone"]),
    )
    check(
        "只有手机号的行被拒绝，原因写进备注",
        sum(1 for r in reps if "官网/来源只公示手机号" in r["note"]) == 9
        and sum(1 for r in reps if "仅有个人手机号" in r["note"]) == 1,
        [r["name"] for r in reps if "电话未收录" in r["note"]][:12],
    )
    check(
        "残号（0574-8749（尾号待补）这类）也不会被拼成一个假号码",
        sum(1 for r in reps if "号码形态不合法或残缺" in r["note"]) == 3
        and all(not r["phone"] for r in reps if "号码形态不合法或残缺" in r["note"]),
        [r["name"] for r in reps if "号码形态不合法或残缺" in r["note"]],
    )
    check(
        "省外「全国代表」54 家（含科大讯飞、商汤等外省龙头）",
        sum(1 for r in reps if r["out_of_province"]) == 54,
        sum(1 for r in reps if r["out_of_province"]),
    )
    doubled = Counter(r["name"] for r in reps if r["name"] in (
        "深圳市兆驰股份有限公司（002429）", "西安铂力特增材技术股份有限公司（688333）"))
    check(
        "同一企业代表多个集群时保留两条记录",
        doubled
        == {"深圳市兆驰股份有限公司（002429）": 2, "西安铂力特增材技术股份有限公司（688333）": 2},
        doubled,
    )
    merged_multi = [c for c in backend_set if len(c["cluster_slugs"]) > 1]
    check(
        "同一企业可以挂多个集群（清单代表 + 产业分组 + 415X 赛道）",
        {"深圳市兆驰股份有限公司（002429）", "西安铂力特增材技术股份有限公司（688333）"}
        <= {c["name"] for c in merged_multi},
        [c["name"] for c in merged_multi][:5],
    )
    # 每一条都必须能落到**已有的**官方集群上：这一份清单不新建集群。
    #
    # 判据必须取自**这份清单自己**的归属（原始解析结果里的 list_cluster_slug），
    # 不能取合并骨架的 cluster_slugs —— 后者是全部名单的并集，
    # 一家同时出现在临安底表里的企业会带上 linan-* 的集群，
    # 于是这条规则会被一个与它无关的集群名判失败。
    reps_source = json.loads((ROOT / "data" / "source" / "cluster_reps.json").read_text(encoding="utf-8"))
    reps_slugs = {r["list_cluster_slug"] for r in reps_source if r.get("list_cluster_slug")}
    reps_companies = [c for c in backend_set if "全国代表" in " ".join(c["list_names"])]
    clusters = reps_slugs
    check("代表企业全部挂到已存在的集群", clusters <= {
        "415X-" + t for t in (
            "人工智能", "低空经济与航空航天", "工业母机与机器人", "新能源与新型储能",
            "新能源汽车及零部件", "智能物联", "现代家居与智能家电", "现代纺织与服装",
            "生物医药与医疗器械", "电气与农机装备", "绿色石化", "集成电路",
            "高端新材料", "高端船舶与海工装备", "高端软件",
        )
    } | {slug for slug in clusters if slug.startswith("newstar-")} | {
        "shantou-gurao-seamless", "foshan-yanbu-brands", "jinjiang-shenhu-underwear",
    }, sorted(clusters)[:5])
    check(
        "「全国代表」清单没有新建集群（义乌无缝/武义户外就是官方新星产业群）",
        all(
            c["cluster_slugs"][0].startswith(("415X-", "newstar-", "shantou-", "foshan-", "jinjiang-"))
            for c in reps_companies
            if c["cluster_slugs"]
        ),
        [
            c["name"] for c in reps_companies
            if c["cluster_slugs"] and not c["cluster_slugs"][0].startswith(("415X-", "newstar-", "shantou-", "foshan-", "jinjiang-"))
        ][:3],
    )

    check(
        "带股票代码/品牌括注的企业另有登记名（验收门要用它）",
        sum(1 for r in reps if r["match_name"] != r["name"]) == 119
        and all(r["name"].startswith(r["match_name"]) for r in reps),
        sum(1 for r in reps if r["match_name"] != r["name"]),
    )
    check(
        "括注在名字中间的登记名保持不变（中航（成都）无人机系统股份有限公司）",
        next(r["match_name"] for r in reps if r["name"].startswith("中航（成都）")) == "中航（成都）无人机系统股份有限公司",
        [r["match_name"] for r in reps if r["name"].startswith("中航")],
    )
    check(
        "骨架带着登记名，验收门不拿展示名去匹配",
        all(c.get("match_name") for c in backend_set if "全国代表" in " ".join(c["list_names"])),
    )

    # cluster.summary / note 在前端是纯文本插值（{{ }}），写进去的 Markdown 会原样显示成 `**`
    research = json.loads((ROOT / "data/curated/research_clusters.json").read_text(encoding="utf-8"))
    markdown_hits = [
        (o.get("slug"), key)
        for group in ("clusters", "overlays")
        for o in research.get(group) or []
        for key in ("note", "summary", "description")
        if isinstance(o.get(key), str) and "**" in o[key]
    ]
    check("集群摘要里没有留 Markdown 粗体标记（前端是纯文本插值）", not markdown_hits, markdown_hits[:3])
    check(
        "武义/义乌的档案不再自称「无官方集群名单」",
        all("无官方集群名单" not in (o.get("note") or "") for o in research.get("overlays") or []),
    )

    print("\n=== 灌库不该删掉账号与收藏；收藏按企业名关联 ===")
    import tempfile  # noqa: E402

    from app.auth import PRESERVED_TABLES, Favorite  # noqa: E402
    from app.config import settings  # noqa: E402
    from app.cli import droppable_tables  # noqa: E402

    droppable = {table.name for table in droppable_tables()}
    check(
        "重新灌库只重建数据层，账号与收藏表不在删除清单里",
        {"app_user", "favorite"}.isdisjoint(droppable),
        sorted(droppable),
    )
    check(
        "删除清单里确实还有数据层表（不是把「全都保留」当成修好了）",
        {"enterprise", "cluster", "region", "enterprise_cluster"} <= droppable,
        sorted(droppable),
    )
    # 结构性约束：收藏一旦外键指向 enterprise，重灌就必须先删它 —— 于是又回到
    # 「更新名单 = 删掉用户数据」。所以这里必须**没有**外键，而且键是企业名。
    favorite_fks = {fk.target_fullname for fk in Favorite.__table__.foreign_keys}
    check(
        "收藏不引用 enterprise（外键会让重灌与用户数据互相绑架）",
        "enterprise.id" not in favorite_fks,
        sorted(favorite_fks),
    )
    check(
        "收藏按企业名唯一（重灌后自增 id 会变，名称才稳定）",
        "enterprise_name" in {c.name for c in Favorite.__table__.columns}
        and any(
            {c.name for c in constraint.columns} == {"user_id", "enterprise_name"}
            for constraint in Favorite.__table__.constraints
            if constraint.__class__.__name__ == "UniqueConstraint"
        ),
        [c.name for c in Favorite.__table__.columns],
    )

    print("\n=== 产业带档案编辑：校验、叠加、撤销 ===")
    from app.services import cluster_edits as ce  # noqa: E402

    _, problems = ce.validate_edit({"metrics": [{"value": 320, "unit": "家"}]})
    check(
        "规模数字必须带口径与年份（裸数字直接拒绝）",
        any("口径" in p for p in problems) and any("年份" in p for p in problems),
        problems,
    )
    _, problems = ce.validate_edit({"honors": [{"name": "某某基地"}]})
    check("荣誉必须写授予主体", any("授予方" in p for p in problems), problems)
    _, problems = ce.validate_edit({"metrics": [
        {"value": 1, "unit": "家", "caliber": "口径", "year": "2024", "source_name": "来源", "source_grade": "官方认定"}
    ]})
    check("规模指标的来源核实程度也要合法", any("来源核实程度" in p for p in problems), problems)
    _, problems = ce.validate_edit({"summary_grade": "official_ish"})
    check("简介的核实程度只能是既定五档", any("只能是" in p for p in problems), problems)
    _, problems = ce.validate_edit({"unknown": 1})
    check("不可编辑的字段会被 schema 挡住（这里兜底报「没有可编辑字段」）", problems != [], problems)
    clean, problems = ce.validate_edit({"aliases": [" 甲 ", "甲", "乙", ""], "note": " 说明 "})
    check(
        "别名去重去空、说明去空格",
        problems == [] and clean["aliases"] == ["乙", "甲"] and clean["note"] == "说明",
        clean,
    )

    original_data_dir = settings.data_dir
    try:
        settings.data_dir = Path(tempfile.mkdtemp())
        record = ce.upsert_edit("demo-cluster", {"summary": "站方写的简介", "summary_grade": "site_curated"})
        check("编辑落盘到 cluster_edits.json", ce.edits_path().exists() and record["slug"] == "demo-cluster")
        check(
            "同一 slug 再编辑是字段级覆盖，不是新增一条",
            len(ce.read_edits()["edits"]) == 1
            and ce.upsert_edit("demo-cluster", {"note": "第二次"})["summary"] == "站方写的简介",
            ce.read_edits()["edits"],
        )

        # 叠加：只动简介与它自己的档位，**不碰集群整体的核实程度**
        class _FakeCluster:
            slug = "demo-cluster"
            summary = ""
            summary_grade = ""
            aliases = []
            metrics = []
            honors = []
            data_grade = "official"
            verified_at = None
            sources = []

        class _FakeSession:
            def flush(self):
                return None

        fake = _FakeCluster()
        applied = ce.apply_cluster_edits(_FakeSession(), {"demo-cluster": fake})
        check(
            "叠加写简介与简介档位",
            applied == 1 and fake.summary == "站方写的简介" and fake.summary_grade == "site_curated",
            (fake.summary, fake.summary_grade),
        )
        check(
            "叠加**不**改集群整体档位（官方认定的集群不该因为站方补了段简介就换标签）",
            fake.data_grade == "official",
            fake.data_grade,
        )
        check("撤销编辑后文件里就没有这条了", ce.delete_edit("demo-cluster") and ce.read_edits()["edits"] == [])
    finally:
        settings.data_dir = original_data_dir

    check(
        "正式的 cluster_edits.json 没留下测试残留",
        all(row.get("slug") != "demo-cluster" for row in ce.read_edits()["edits"]),
        ce.read_edits()["edits"],
    )

    print("\n=== 浙江省镇街级块状经济企业清单：自带归属、对标龙头、号码性质 ===")
    from app.services.list_import import CLUSTER_SYNONYMS, cluster_slug_index  # noqa: E402

    town = json.loads((ROOT / "data" / "source" / "town_clusters.json").read_text(encoding="utf-8"))
    check("行数为 264（46 个产区 × 5-7 家）", len(town) == 264, len(town))
    check(
        "档位分布与清单一致（A79 / B133 / C40 / 未标 12）",
        Counter(r["verify_tier"] for r in town) == {"A": 79, "B": 133, "C": 40, "—": 12},
        Counter(r["verify_tier"] for r in town),
    )
    check("没有任何未识别的官网类型", not any(r["website_kind"] not in {
        "企业官网", "集团官网", "品牌海外站", "第三方商铺", "无独立官网", "待补", ""
    } for r in town), Counter(r["website_kind"] for r in town))

    # ① 自带归属：46 个产区必须全部落到**已有**集群，且每个产区都有企业
    index = cluster_slug_index()
    seed_clusters = json.loads((ROOT / "data" / "curated" / "clusters_seed.json").read_text(encoding="utf-8"))["clusters"]
    known = {c["slug"] for c in seed_clusters}
    slugs = {r["list_cluster_slug"] for r in town}
    check(
        "46 个产区全部落位到已存在的集群（一行都没挂空）",
        "" not in slugs and len(slugs) == 46 and slugs <= known,
        sorted(slugs - known) or sorted(slugs)[:3],
    )
    per_cluster = Counter(r["list_cluster_slug"] for r in town)
    check("每个产区至少 5 家企业（不是空壳档案）", min(per_cluster.values()) >= 5, per_cluster.most_common(3))
    check(
        "同名产区指向已有档案，不新建（塘下汽摩配→瑞安汽摩配 等 4 处）",
        all(CLUSTER_SYNONYMS[name] in known for name in CLUSTER_SYNONYMS)
        and CLUSTER_SYNONYMS["塘下汽摩配"] == "ruian-qimopei"
        and "ruian-qimopei" not in {c["slug"] for c in seed_clusters if c["name"] == "塘下汽摩配"},
        CLUSTER_SYNONYMS,
    )
    # 两条同名产区必须挂到同一个集群，否则同一个产业带会出现两条档案
    same = [
        ("ruian-qimopei", "瑞安汽摩配", "塘下汽摩配"),
        ("shangyu-sanye", "上虞伞业", "崧厦伞业"),
        ("diankou-wujin", "店口五金", "店口铜加工"),
    ]
    check(
        "同一条产业带的两种叫法没有各自建档案",
        all(
            len([c for c in seed_clusters if c["slug"] == slug]) == 1
            and not any(c["name"] == alias for c in seed_clusters)
            for slug, _, alias in same
        ),
        same,
    )

    # ② 对标龙头：18 家，不参与**产区**统计 —— 但「非本地产区」不等于「不在浙江」。
    # 14 家在外省/境外（施耐德上海、立讯东莞、新秀丽境外…），4 家在浙江
    # （天堂伞萧山、均胜宁波、森马与人本温州）。省外与否只看企业**自己的地址**，
    # 把 18 家一律记成省外，就会把 4 家浙江企业划出浙江统计。
    benchmarks = [r for r in town if "对标" in r["note"] or "非本地" in r["note"]]
    out_province = [r for r in benchmarks if r["out_of_province"]]
    in_zhejiang = [r for r in benchmarks if not r["out_of_province"]]
    check(
        "对标龙头共 18 家，其中 14 家省外、4 家在浙江（按各自地址判）",
        len(benchmarks) == 18 and len(out_province) == 14 and len(in_zhejiang) == 4,
        (len(benchmarks), len(out_province), [r["name"] for r in in_zhejiang]),
    )
    check(
        "在浙江的对标龙头记下自己的市，而不是产区的县市区",
        {r["name"]: r["city"] for r in in_zhejiang}
        == {
            "杭州天堂伞业集团有限公司": "杭州市",
            "宁波均胜电子股份有限公司": "宁波市",
            "巴拉巴拉（浙江森马服饰股份有限公司）": "温州市",
            "人本集团有限公司": "温州市",
        },
        [(r["name"], r["city"], r["district"]) for r in in_zhejiang],
    )
    check(
        "省外对标龙头不带「本地产区」的县市区（那是产区的地址，不是它的）",
        all(not r["district"] for r in out_province),
        [(r["name"], r["district"]) for r in out_province if r["district"]][:3],
    )
    check(
        "「上海市普陀区」不会被认成舟山普陀区（浙江地名与外省重名要先排除）",
        all(r["city"] != "舟山市" for r in town if "上海" in r["raw"]["address"]),
        [r["name"] for r in town if "上海" in r["raw"]["address"] and r["city"] == "舟山市"],
    )
    check(
        "对标龙头的来源说明留在备注里（地址列写的是「仅作对标」这类注释）",
        all("对标" in r["note"] for r in benchmarks),
        [r["name"] for r in benchmarks if "对标" not in r["note"]][:3],
    )
    # 调研文档自己写的是「本地 246 家 + 全国对标龙头 18 家」——那是**产区归属**，
    # 不是省域归属。本站按地址判省外，所以浙江这一侧是 246 + 4 家浙江省内的对标龙头。
    check(
        "浙江 250 家（本地 246 + 省内对标龙头 4）/ 省外 14 家",
        sum(1 for r in town if r["out_of_province"]) == 14
        and sum(1 for r in town if not r["out_of_province"]) == 250,
        (sum(1 for r in town if r["out_of_province"]), len(town)),
    )

    # ③ 号码性质：这类清单里「有官网」与「打得通」是两件事，必须逐行带出去
    layer = json.loads((ROOT / "data" / "curated" / "town_clusters_list.json").read_text(encoding="utf-8"))
    check(
        "联系方式层带上了号码性质（官网总机 / 第三方总机 / 待核）",
        sum(1 for r in layer if r.get("phone_nature")) >= 100,
        Counter(r.get("phone_nature") for r in layer).most_common(4),
    )
    check(
        "「待补（原因）」的原因写进了备注，而不是只剩一片空白",
        sum(1 for r in town if "电话待补：" in r["note"]) >= 5
        and any("打码" in r["note"] for r in town),
        [r["name"] for r in town if "电话待补：" in r["note"]][:3],
    )
    check(
        "残号（打码 / 旧号）与「来源没给」分开处理",
        any("号码形态不合法或残缺" in r["note"] for r in town)
        and any("电话待补：" in r["note"] for r in town),
    )
    check(
        "手机号一律不收录，且原因写清楚",
        not any(re.fullmatch(r"1[3-9]\d{9}", re.sub(r"\D", "", r["phone"])) for r in town if r["phone"])
        and any("手机号" in r["note"] for r in town),
        [r["name"] for r in town if "手机号" in r["note"]][:3],
    )
    # 国际写法 +86-512-…：国家码要剥掉，长途冠码 0 要补回来
    from app.services.list_import import clean_phone  # noqa: E402

    check(
        "国际写法的座机（+86-512-66615637）能还原成 0512-66615637",
        clean_phone("+86-512-66615637")[0] == "0512-66615637"
        and clean_phone("+86-769-38800888")[0] == "0769-38800888",
        (clean_phone("+86-512-66615637"), clean_phone("+86-769-38800888")),
    )
    check(
        "国际写法的手机号仍是手机号（补 0 不会把它变成座机）",
        clean_phone("+86-13512345678")[0] == "",
        clean_phone("+86-13512345678"),
    )

    # ④ 「—」这类破折号哨兵：整串匹配，不能当子串（否则会灭掉所有带连字符的域名）
    check(
        "带连字符的域名不会被「—」哨兵误杀",
        any(r["website"].endswith("qj-robot.com") or "-" in r["website"] for r in town if r["website"]),
        [r["website"] for r in town if r["website"]][:3],
    )

    print("\n=== 临安区规上企业基本信息（政府公开数据）===")
    reg = json.loads((ROOT / "data" / "source" / "linan_registry.json").read_text(encoding="utf-8"))
    check("行数为 593（594 − 1 家注销）", len(reg) == 593, len(reg))
    check(
        "这张表只有主体信息，没有任何联系方式",
        not any(r["website"] or r["phone"] or r["email"] or r["address"] for r in reg),
        [(r["name"], r["phone"]) for r in reg if r["phone"]][:3],
    )
    check(
        "产区归属全部来自「区级产业分组」，且 13 个产区都落位",
        {r["list_cluster_basis"] for r in reg} == {"区级产业分组"}
        and len({r["list_cluster_slug"] for r in reg}) == 13,
        sorted({r["list_cluster_slug"] for r in reg}),
    )
    check(
        "每个产区都至少 1 家企业（没有空壳档案）",
        min(Counter(r["list_cluster_slug"] for r in reg).values()) >= 1,
        Counter(r["list_cluster_slug"] for r in reg).most_common(3),
    )
    check(
        "统一社会信用代码与成立年份全部解析出来了",
        all(r["credit_code"] for r in reg) and all(r["founded_year"] for r in reg),
        [r["name"] for r in reg if not r["founded_year"]][:3],
    )
    check(
        "成立年份落在合理区间（1978-2022）",
        min(r["founded_year"] for r in reg) == 1978 and max(r["founded_year"] for r in reg) == 2022,
        (min(r["founded_year"] for r in reg), max(r["founded_year"] for r in reg)),
    )
    check(
        "地区由「所属街道」反推（这份表没有城市/县列）",
        sum(1 for r in reg if r["district"]) == len(reg) - 2  # 2 家所属街道是「部门」
        and {r["city"] for r in reg if r["city"]} == {"杭州市"},
        Counter((r["city"], r["district"]) for r in reg).most_common(2),
    )
    check(
        "「科技城」被认成青山湖街道（同一个实体的两种写法）",
        next(r for r in reg if r["town"] == "科技城")["district"] == "临安区",
    )
    # 个人信息：底表里有法人姓名/组织机构代码（已打码），仍一律不存
    raw_keys = {k for r in reg for k in r.get("raw", {})}
    check(
        "法人姓名与组织机构代码没有被留档",
        not raw_keys & {"法人姓名", "组织机构代码", "创建人", "更新人"},
        sorted(raw_keys & {"法人姓名", "组织机构代码"}),
    )
    check(
        "原始行的其余字段完整留档（信用代码/行业/街道都在）",
        {"企业统一信用代码", "所属行业名称", "所属街道", "产业名称"} <= raw_keys,
        sorted(raw_keys)[:6],
    )
    # 档位：这份底表本身是 gov_open，但 9 家同时出现在官方名单里的企业
    # 取**更高**的那一档（官方认定优先）。这是既定的合并规则，
    # 不是这次导入引入的行为 —— 断言要反映它，而不是要求「全部 gov_open」。
    registry_only = [
        c for c in backend_set
        if "临安区规上" in " ".join(c["list_names"]) and len(c["list_names"]) == 1
    ]
    check(
        "只出现在这份底表里的企业档位是「政府公开信息」",
        # 583 → 581（415X 调研清单收录 2 家）→ 532（浙江省产业带调研清单收录 49 家）。
        # 这些企业现在都有两份名单，不再是「只出现一次」。
        len(registry_only) == 532 and all(c["data_grade"] == "gov_open" for c in registry_only),
        len(registry_only),
    )
    # 档位优先级现在是显式的（GRADE_RANK）：以前只写 `if grade == "official"`，
    # 三档之后那样写会变成「取决于名单读取顺序」。
    registry_all = [c for c in backend_set if "临安区规上" in " ".join(c["list_names"])]
    check(
        "同时在官方名单里的 9 家取「官方认定」（官方优先，不因底表降级）",
        sum(1 for c in registry_all if c["data_grade"] == "official") == 9,
        [c["name"] for c in registry_all if c["data_grade"] == "official"][:3],
    )
    check(
        "「政府公开」高于「站方整理」（底表 + 调研清单同在时取政府公开）",
        next(c for c in registry_all if c["name"] == "浙江吉祥电缆制造有限公司")["data_grade"] == "gov_open",
        [(c["name"], c["data_grade"]) for c in registry_all if len(c["list_names"]) > 1][:3],
    )
    check(
        "注销企业没有进库",
        all("天目铸造" not in c["name"] for c in backend_set),
    )

    print("\n=== 名单导入接口：解析规则与注册表 ===")
    import tempfile
    from app.config import settings  # noqa: E402
    from app.services import list_import as li  # noqa: E402

    def parse(text: str, **kwargs):
        kwargs.setdefault("list_name", "测试名单（第三方调研）")
        kwargs.setdefault("grade", "site_curated")
        return li.parse_list_csv(text, **kwargs)

    csv_head = "公司名称,官网,官网类型,电话,地址,核实等级,备注\n"
    # ① 哨兵值：这些字符串都「非空」，`if website:` 会把它们全当成有官网
    sentinel = parse(
        csv_head
        + "甲公司,无官网（官网为占位符）,无独立官网,待补,杭州市西湖区文三路1号,A 官网公示,\n"
        + "乙公司,待补（母公司创世纪 300083）,待补,待补,杭州市余杭区文一西路2号,B 工商可核,\n"
        + "丙公司,1688店铺,第三方商铺,待补,金华市义乌市北苑街道3号,C 第三方线索,\n"
    )
    check(
        "哨兵值一个都不进官网/电话字段",
        all(not r["website"] and not r["phone"] for r in sentinel["rows"]),
        [(r["name"], r["website"], r["phone"]) for r in sentinel["rows"]],
    )
    check(
        "第三方商铺页的链接不进官网字段，且写明原因",
        sentinel["rows"][2]["website"] == "" and "第三方商铺" in sentinel["rows"][2]["note"],
        sentinel["rows"][2]["note"],
    )
    check("原始值留在 raw 里备查", sentinel["rows"][0]["raw"]["website"] == "无官网（官网为占位符）")

    # ② 「待核」的裸域名：作者只对「是不是这家企业的站」没把握 → 不发布，但当候选送进门
    pending = parse(csv_head + "丁公司,99hxy.com,待核,0579-88701110,金华市兰溪市黄店镇,A 官网公示,\n")
    check(
        "「待核」的裸域名不发布为官网，但作为候选留下",
        pending["rows"][0]["website"] == ""
        and pending["rows"][0]["candidate_website"] == "https://99hxy.com"
        and "仅作候选" in pending["rows"][0]["note"],
        pending["rows"][0],
    )
    check("候选域名进候选层（它必须过验收门才会被当成官网）", len(pending["sites"]) == 1, pending["sites"])

    # ③ 没见过的官网类型：默认当成「企业官网」等于替清单做判断
    unknown = parse(csv_head + "戊公司,example.com,经销商站,0571-88888888,杭州市滨江区,A 官网公示,\n")
    check(
        "未识别的官网类型一律不发布，并报出来",
        unknown["rows"][0]["website"] == ""
        and unknown["rows"][0]["website_kind"] == ""
        and unknown["report"]["unknown_website_kinds"] == ["经销商站"]
        and any("官网类型未识别" in w for w in unknown["report"]["warnings"]),
        unknown["report"]["warnings"],
    )
    check(
        "集团/母公司站点收，但必须带链接类型（页面据此说明它指向集团）",
        all(
            r["website"] and r["website_kind"] == kind
            for kind, r in (
                ("集团官网", parse(csv_head + "己公司,group.com,集团官网,0571-88888888,杭州市滨江区,A 官网公示,\n")["rows"][0]),
                ("母公司官网", parse(csv_head + "庚公司,parent.com,母公司官网,0571-88888888,杭州市滨江区,A 官网公示,\n")["rows"][0]),
            )
        ),
    )

    # ④ 电话号码形态
    phones = parse(
        csv_head
        + "辛公司,a.com,企业官网,0576-87212222；0576-87370888,台州市黄岩区,A 官网公示,\n"
        + "壬公司,b.com,企业官网,15525124990；17740868515,台州市黄岩区,B 工商可核,\n"
        + "癸公司,c.com,企业官网,0574-8749（尾号待补）,宁波市海曙区,B 工商可核,\n"
        + "子公司,d.com,企业官网,025-52785866,南京市江宁区,C 第三方线索,\n"
    )["rows"]
    check(
        "多号码取第一个合法座机（不是把整串判失败）",
        phones[0]["phone"] == "0576-87212222",
        phones[0]["phone"],
    )
    check("全是手机号的行被拒绝，并写明原因", phones[1]["phone"] == "" and "手机号" in phones[1]["note"], phones[1]["note"])
    check(
        "残号不会被拼成一个假号码",
        phones[2]["phone"] == "" and "号码形态不合法或残缺" in phones[2]["note"],
        phones[2],
    )
    check(
        "3 位区号（025）不会被切成 0255 —— 区号长度按首位的字形判断",
        phones[3]["phone"] == "025-52785866",
        phones[3]["phone"],
    )

    # ⑤ 展示名带括注：验收门拿它去页面里找永远匹配不上
    names = parse(
        csv_head
        + "科大讯飞股份有限公司（002230）,a.com,企业官网,0551-65303305,合肥市高新区,A 官网公示,\n"
        + "中航（成都）无人机系统股份有限公司,b.com,企业官网,028-86786299,成都市青羊区,A 官网公示,\n"
    )["rows"]
    check("结尾的股票代码括注要摘掉（登记名才是页面上的写法）", names[0]["match_name"] == "科大讯飞股份有限公司")
    check(
        "名字**中间**的括注是法定名称的一部分，必须保留",
        names[1]["match_name"] == "中航（成都）无人机系统股份有限公司",
        names[1]["match_name"],
    )
    check(
        # 科大讯飞不在余杭那个集群里，它在合肥。地址写的是「合肥市高新区」——
        # 「合肥市」进不了 `DISTRICTS`（那是浙江的区划表），认出浙江才怪，
        # 所以判据是「省外」；而市名从地址里取得到，就带上（页面要显示地区）。
        # 早先这里连市名都不带，是因为怕把「集群（县市区）」的产区坐标当企业所在地 ——
        # 现在列值只在**浙江行**上被当成产区坐标，外省行的市名就是企业自己的。
        "企业所在地从自己的地址认，不是从「集群（县市区）」",
        names[0]["city"] == "合肥市" and names[0]["out_of_province"],
        (names[0]["city"], names[0]["out_of_province"]),
    )

    # ⑥ 档位文案：认不出来就说不知道，别编一个含义
    tiers = parse(
        csv_head
        + "丑公司,a.com,企业官网,0571-88888888,杭州市滨江区,A 官网公示,\n"
        + "寅公司,b.com,企业官网,0571-88888888,杭州市滨江区,D 报价确认,\n",
        grade="site_curated",
    )
    labels = {r["name"]: r["source_label"] for r in tiers["companies"]}
    check("A 档标签写「官网公示（未复核）」", "官网公示（未复核）" in labels["丑公司"], labels["丑公司"])
    check(
        "认不出的档位文案退回「未复核」并报 warning",
        labels["寅公司"] == "第三方调研 · 未复核" and any("档位文案无法分类" in w for w in tiers["report"]["warnings"]),
        (labels["寅公司"], tiers["report"]["warnings"]),
    )

    # ⑥-2 「产区」列：全国性清单一家企业挂哪个集群，由清单自己写的产区决定。
    # 这条规则之前只在解析脚本里（tools/parse_seamless.py），接口导入时 137 行
    # 会全部挂不上集群 —— 而接口才是官方推荐的路径，所以规则也得在这里。
    #
    # 这份夹具用**真实列名**（含产区/地市/县市区）：产区这一列的价值就在于
    # 「清单自己声明归属」，少了行政列就退化成按地址猜，也就测不到这条规则。
    area_head = (
        "序号,批次,产区,地市,县市区,镇街,公司名称,官网,官网类型,电话,电话性质,"
        "地址,地址类型,核实等级,备注\n"
    )
    areas = parse(
        area_head
        + "1,第一批,义乌无缝针织,金华市,义乌市,苏溪镇,巳公司,a.com,企业官网,0579-88888888,总机（官网）,浙江省义乌市苏溪镇民丰路1号,注册地（工商）,A,\n"
        + "2,第一批,汕头谷饶针织内衣,汕头市,潮阳区,谷饶镇,午公司,b.com,无独立官网,0754-88888888,第三方总机,汕头市潮阳区谷饶镇华光工业区,注册地（工商）,B,\n"
        + "3,第一批,汕头潮南内衣,汕头市,潮南区,陈店镇,未公司,c.com,无独立官网,0754-88888888,第三方总机,汕头市潮南区陈店镇洋新村,注册地（工商）,B,\n"
        + "4,第一批,浦江针织服装,金华市,浦江县,白马镇,申公司,d.com,无独立官网,0579-88888888,第三方总机,浦江县白马镇金腾路1号,注册地（工商）,B,\n"
        + "5,第一批,其他产区,揭阳市,普宁市,池尾街道,酉公司,e.com,无独立官网,,待核,普宁市池尾街道钟潭竹园93号,注册地（工商）,B,\n",
        list_name="产区测试（第三方调研）",
    )["rows"]
    by_name = {r["name"]: r for r in areas}
    check(
        "产区列落到集群：义乌→官方新星、谷饶/潮南→省外对比产区、浦江→省内调研产区",
        [by_name[n]["list_cluster_slug"] for n in ("巳公司", "午公司", "未公司", "申公司")] == [
            "newstar-18-无缝服装", "shantou-gurao-seamless",
            "shantou-chaonan-underwear", "pujiang-zhenzhi-fuzhuang",
        ],
        [by_name[n]["list_cluster_slug"] for n in ("巳公司", "午公司", "未公司", "申公司")],
    )
    check(
        "归属依据记的是「清单产区列」，产区原值也留着",
        all(by_name[n]["list_cluster_basis"] == "清单产区列" for n in ("巳公司", "申公司"))
        and by_name["申公司"]["production_area"] == "浦江针织服装",
        by_name["申公司"],
    )
    check(
        "省外产区标成省外（否则会混进「浙江企业数」），地区照留",
        [by_name[n]["out_of_province"] for n in ("巳公司", "午公司", "申公司")] == [False, True, False]
        and by_name["午公司"]["district"] == "潮阳区"
        and by_name["未公司"]["city"] == "汕头市",
        [(by_name[n]["city"], by_name[n]["district"], by_name[n]["out_of_province"]) for n in ("午公司", "未公司")],
    )
    check(
        "省外地市名（揭阳）即使清单没标产区，也要判成省外",
        by_name["酉公司"]["out_of_province"] and by_name["酉公司"]["city"] == "揭阳市",
        (by_name["酉公司"]["city"], by_name["酉公司"]["out_of_province"]),
    )
    check(
        "「其他产区」不是「未识别」：不挂集群，但不报 warning",
        by_name["酉公司"]["list_cluster_slug"] == ""
        and parse(
            area_head
            + "1,第一批,其他产区,揭阳市,普宁市,池尾街道,戌公司,a.com,无独立官网,,待核,普宁市池尾街道,A,B,\n",
            list_name="产区测试（第三方调研）",
        )["report"]["unknown_production_areas"] == {},
    )
    unknown_area = parse(
        area_head
        + "1,第一批,义乌无缝针织厂,金华市,义乌市,苏溪镇,亥公司,a.com,无独立官网,,待核,义乌市苏溪镇,A,B,\n",
        list_name="产区测试（第三方调研）",
    )
    check(
        "认不出来的产区值要报出来（新增产区时不该静默丢归属）",
        unknown_area["report"]["unknown_production_areas"] == {"义乌无缝针织厂": 1}
        and any("产区列的值未识别" in w for w in unknown_area["report"]["warnings"]),
        unknown_area["report"]["warnings"],
    )

    # ⑥-3 整份清单的归属声明（`default_cluster_slug`）。
    # 《现代纺织与服装企业调研清单》317 行整份属于 415X「现代纺织与服装」赛道，
    # 但其中 58 行自带「块状经济」列（大陈衬衫…）——那时各自的镇街产区更具体，
    # 整份声明只能兜底、不能覆盖。
    default_head = (
        "序号,批次,块状经济,产业,地市,县市区,镇街,公司名称,官网,官网类型,电话,电话性质,"
        "地址,地址类型,核实等级,备注\n"
    )
    default_text = (
        default_head
        + "1,块状经济,大陈衬衫,现代纺织·衬衫,金华市,义乌市,大陈镇,甲一公司,a.com,企业官网,0579-88888888,总机（官网）,义乌市大陈镇,A,B,\n"
        + "2,品类补充,,现代纺织·化纤,苏州市,吴江区,,甲二公司,b.com,企业官网,0512-88888888,总机（官网）,苏州市吴江区,A,B,\n"
    )
    with_default = parse(
        default_text,
        list_name="整份归属测试（第三方调研）",
        default_cluster_slug="415X-现代纺织与服装",
    )["rows"]
    by_names = {r["name"]: r for r in with_default}
    check(
        "整份归属只兜底、不覆盖逐行自带的「块状经济」",
        by_names["甲一公司"]["list_cluster_slug"] == "yiwu-dachen"
        and by_names["甲一公司"]["list_cluster_basis"] == "块状经济"
        and by_names["甲二公司"]["list_cluster_slug"] == "415X-现代纺织与服装"
        and by_names["甲二公司"]["list_cluster_basis"] == "整份清单归属",
        [(r["name"], r["list_cluster_slug"], r["list_cluster_basis"]) for r in with_default],
    )
    check(
        "没给整份归属时逐行决定（留空 ≠ 悄悄挂到某处）",
        all(
            not r["list_cluster_slug"]
            for r in parse(default_text, list_name="整份归属测试（第三方调研）")["rows"]
            if r["name"] != "甲一公司"
        ),
    )

    # ⑥-4 「集群类型」列（第四批产业群名单语境）：核心区/协同区 + 赛道 → 415X-<赛道>。
    # 通用导入器原先既不认这三列、也不给记录填 track，于是「集群代表企业」那批行
    # 走接口导入时会全部挂不上它们**代表**的赛道集群（此前只走 tools/parse_*.py 才行）。
    type_head = (
        "序号,批次,集群类型,所属产业赛道,细分赛道,集群（县市区）,公司名称,官网,官网类型,"
        "电话,电话性质,地址,地址类型,核实等级,备注\n"
    )
    typed = parse(
        type_head
        + "1,第一批,核心区,现代纺织与服装,,长兴县,乙一公司,a.com,企业官网,0572-88888888,总机（官网）,长兴县,A,B,\n"
        + "2,第一批,新星产业群,,无缝服装,义乌市,乙二公司,b.com,企业官网,0579-88888888,总机（官网）,义乌市,A,B,\n"
        + "3,第一批,写着玩,现代纺织与服装,,长兴县,乙三公司,c.com,企业官网,0572-88888888,总机（官网）,长兴县,A,B,\n",
        list_name="集群类型测试（第三方调研）",
    )
    typed_rows = {r["name"]: r for r in typed["rows"]}
    check(
        "「核心区」+赛道 → 挂对应的 415X 集群，并记下归属依据",
        typed_rows["乙一公司"]["list_cluster_slug"] == "415X-现代纺织与服装"
        and typed_rows["乙一公司"]["list_cluster_basis"] == "清单集群类型（核心区）",
        typed_rows["乙一公司"],
    )
    check(
        "赛道名要落到记录里（415X 的「赛道」会写进 industry_l1，不能拿它去猜集群）",
        typed_rows["乙一公司"]["track"] == "现代纺织与服装",
        typed_rows["乙一公司"]["track"],
    )
    check(
        "「新星产业群」交给 seed 侧按县市区+细分赛道匹配，不算「未识别」",
        typed_rows["乙二公司"]["list_cluster_slug"] == ""
        and "新星产业群" not in typed["report"]["unknown_cluster_types"]
        and typed_rows["乙二公司"]["sub_track"] == "无缝服装",
        (typed_rows["乙二公司"], typed["report"]["unknown_cluster_types"]),
    )
    check(
        "认不出来的集群类型要报出来（否则几百行的归属静默挂空）",
        typed["report"]["unknown_cluster_types"] == {"写着玩": 1}
        and any("集群类型未识别" in w for w in typed["report"]["warnings"]),
        typed["report"]["warnings"],
    )

    # ⑥-4b 「产业带」列（浙江省产业带企业调研清单）。
    #
    # 这一列此前**完全不被识别**（别名里只有「块状经济」），于是 594 行
    # 全部没有集群归属，而报告显示「集群归属 0 / 0 行」—— 既不是全落位、
    # 也不是全落空，而是**什么都没算**，看起来却像正常结论。
    #
    # 同时它踩到另一颗雷：`LINAN_INDUSTRY_CLUSTERS`（临安区级产业分组表）
    # 原先被无条件查询，「集成电路」这种又当分组名又当 415X 赛道名的值
    # 会被它截走，24 行（萧山/北仑/富阳/滨江/越城）全被挂到临安集成电路。
    belt_head = "序号,产业带,所属产业,地市,县市区,公司名称,官网,官网类型,电话,核实等级,备注\n"
    belt = parse(
        belt_head
        + "1,柳市低压电器,电气·低压电器,温州市,乐清市,丙一公司,,无独立官网,,B,\n"
        + "2,集成电路,新一代信息技术·集成电路,杭州市,萧山区,丙二公司,,无独立官网,,B,\n"
        + "3,临安集成电路,新一代信息技术·集成电路,杭州市,临安区,丙三公司,,无独立官网,,B,\n"
        + "4,智能物联,新一代信息技术·智能物联,杭州市,滨江区,丙四公司,,无独立官网,,B,\n"
        + "5,临安区创新药与医疗器械,生物医药,杭州市,临安区,丙五公司,,无独立官网,,B,\n",
        list_name="产业带测试（第三方调研）",
    )
    belt_rows = {r["name"]: r for r in belt["rows"]}
    check(
        "「产业带」列能识别，并按名字落到已有集群",
        belt_rows["丙一公司"]["list_cluster_slug"] == "liushi-dianqi"
        and belt_rows["丙四公司"]["list_cluster_slug"] == "415X-智能物联"
        and belt_rows["丙五公司"]["list_cluster_slug"] == "newstar-14-创新药与医疗器械",
        [(n, r["list_cluster_slug"]) for n, r in belt_rows.items()],
    )
    check(
        "赛道名与「区级产业分组」同名时，产业带列按赛道落位（不被临安分组表截走）",
        belt_rows["丙二公司"]["list_cluster_slug"] == "415X-集成电路"
        and belt_rows["丙三公司"]["list_cluster_slug"] == "linan-jichengdianlu",
        [(n, r["list_cluster_slug"]) for n, r in (("丙二公司", belt_rows["丙二公司"]), ("丙三公司", belt_rows["丙三公司"]))],
    )
    check(
        "归属依据记的是实际列名「产业带」，不是笼统的「块状经济」",
        all(r["list_cluster_basis"] == "产业带" for r in belt["rows"]),
        {r["list_cluster_basis"] for r in belt["rows"]},
    )
    # 反方向：真来自「区级产业分组」列时，那张表仍然要生效（别把修复做成了禁用）
    from app.services.list_import import dispatch_cluster, cluster_slug_index  # noqa: E402

    group_index = cluster_slug_index()
    check(
        "「区级产业分组」列仍走临安分组表（限定依据，不是禁用）",
        dispatch_cluster("集成电路", group_index, "industry_group") == "linan-jichengdianlu"
        and dispatch_cluster("集成电路", group_index, "cluster_name") == "415X-集成电路",
        (
            dispatch_cluster("集成电路", group_index, "industry_group"),
            dispatch_cluster("集成电路", group_index, "cluster_name"),
        ),
    )

    # ⑥-5 「赛道」列单独出现时的落位（415X 调研清单 779 行）。
    #
    # 这份清单没有「集群类型」也没有「产区」，只有一列「赛道」。
    # 此前的 resolve_cluster_slug 只认集群类型/产区/整份归属，于是 21 个赛道
    # **全部**落空 —— 导入报告写着「可落位 0 / 21」，而接口仍回 200「成功」。
    # 结果就是：779 行进来了，集群页却看不到它们。
    #
    # 现在按两条有据可依的规则落位，且都要求「证据在清单或官方名单里」：
    #   1. 赛道名 = 官方 415X 赛道名 → 415X-<赛道>
    #   2. 赛道名 = 官方「新星」产业群名，且**该企业所在县就是那个产业群的县**
    # 做不到就不落位；绝不按字形相近去猜（挂错比不挂更难发现）。
    from app.seed.companies import known_415x_slugs, resolve_cluster_slug  # noqa: E402

    track_ns = {
        ("富阳区", "创新药与医疗器械"): "newstar-13-创新药与医疗器械",
        ("天台经济开发区", "仪器仪表"): "newstar-11-仪器仪表",
        ("奉化区", "精密泵阀"): "newstar-07-精密泵阀",
        ("兰溪市", "中医药大健康"): "newstar-17-中医药大健康",
        ("天台县", "中医药大健康"): "newstar-21-中医药大健康",
    }

    def slug_for(track, district, **extra):
        return resolve_cluster_slug(
            {"list_name": "赛道测试（第三方调研）", "track": track, "district": district, **extra},
            track_ns,
        )

    check(
        "赛道名就是官方 415X 赛道 → 直挂该集群",
        slug_for("人工智能", "余杭区") == "415X-人工智能"
        and slug_for("现代家居与智能家电", "慈溪市") == "415X-现代家居与智能家电"
        and "415X-人工智能" in known_415x_slugs(),
        (slug_for("人工智能", "余杭区"), slug_for("现代家居与智能家电", "慈溪市")),
    )
    check(
        "赛道名是「新星」产业群且企业在那个县 → 挂该县的产业群",
        slug_for("中医药大健康", "兰溪市") == "newstar-17-中医药大健康"
        and slug_for("中医药大健康", "天台县") == "newstar-21-中医药大健康",
        (slug_for("中医药大健康", "兰溪市"), slug_for("中医药大健康", "天台县")),
    )
    check(
        "同一个赛道名在多个县都有产业群、而企业不在其中任何一个县时不猜",
        slug_for("中医药大健康", "滨江区") == ""
        and slug_for("光电（半导体）", "滨江区") == "",
        (slug_for("中医药大健康", "滨江区"), slug_for("光电（半导体）", "滨江区")),
    )
    check(
        "县市区写法差异要归一：撤市设区（奉化市≈奉化区）、开发区挂在县名下（天台县≈天台经济开发区）",
        slug_for("精密泵阀与仪器仪表", "奉化市") == "newstar-07-精密泵阀"
        and slug_for("精密泵阀与仪器仪表", "奉化区") == "newstar-07-精密泵阀"
        and slug_for("精密泵阀与仪器仪表", "天台县") == "newstar-11-仪器仪表",
        (
            slug_for("精密泵阀与仪器仪表", "奉化市"),
            slug_for("精密泵阀与仪器仪表", "奉化区"),
            slug_for("精密泵阀与仪器仪表", "天台县"),
        ),
    )
    check(
        "赛道完全对不上任何已有集群时不落位（不硬凑）",
        slug_for("不存在的赛道", "杭州市") == "",
        slug_for("不存在的赛道", "杭州市"),
    )
    check(
        "行内已有「集群类型」时以它为准，赛道列不再兜底（两份依据不打架）",
        slug_for("人工智能", "长兴县", cluster_type="核心区") == "415X-人工智能"
        and resolve_cluster_slug(
            {"list_name": "赛道测试（第三方调研）", "track": "中医药大健康",
             "district": "兰溪市", "cluster_type": "新星产业群", "sub_track": "无此群"},
            track_ns,
        )
        == "",
        resolve_cluster_slug(
            {"list_name": "赛道测试（第三方调研）", "track": "中医药大健康",
             "district": "兰溪市", "cluster_type": "新星产业群", "sub_track": "无此群"},
            track_ns,
        ),
    )

    # ⑦ 非法输入必须在落盘前被挡住：半个名单比旧名单更糟
    for label, kwargs, text in (
        ("缺少企业名称列", {}, "序号,官网\n1,a.com\n"),
        ("没有数据行", {}, "公司名称,官网\n"),
    ):
        try:
            parse(text, **kwargs)
            check(f"{label}要被拒绝", False, "没有报错")
        except ValueError as exc:
            check(f"{label}要被拒绝", True, str(exc)[:40])
    for bad_key in ("Cluster-Reps", "a", "带空格 key"):
        try:
            li.import_list(text=csv_head + "卯公司,a.com,企业官网,0571-88888888,杭州市滨江区,A,\n",
                           list_name="x", grade="site_curated", source_key=bad_key, dry_run=True)
            check(f"非法 source_key「{bad_key}」要被拒绝", False, "没有报错")
        except ValueError:
            check(f"非法 source_key「{bad_key}」要被拒绝", True)
    try:
        li.import_list(text=csv_head, list_name="x", grade="没这个档", source_key="ok_key", dry_run=True)
        check("未知核实程度要被拒绝", False, "没有报错")
    except ValueError:
        check("未知核实程度要被拒绝", True)

    # ⑧ 落盘：一次导入 = 三份产物 + 注册表条目（用临时目录，别动真数据）
    original_data_dir = settings.data_dir
    try:
        settings.data_dir = Path(tempfile.mkdtemp())
        written = li.import_list(
            text=csv_head + "辰公司,newco.com,企业官网,0571-88888888,杭州市滨江区,A 官网公示,\n",
            list_name="临时测试名单（第三方调研）",
            grade="site_curated",
            source_key="tmp_list",
            source_file="tmp.csv",
            used_for="测试用",
        )["report"]["outputs"]
        check(
            "导入写出三份产物",
            all(Path(path).exists() for path in written.values()),
            [Path(path).name for path in written.values()],
        )
        registry = li.read_registry()["lists"]
        check(
            "注册表记下了名单（下游据此自动发现它，不必改代码）",
            len(registry) == 1 and registry[0]["source_key"] == "tmp_list"
            and registry[0]["rows"] == 1 and registry[0]["used_for"] == "测试用",
            registry,
        )
        check(
            "再次导入同一个 source_key 是覆盖，不是追加",
            li.import_list(
                text=csv_head + "辰公司,newco.com,企业官网,0571-88888888,杭州市滨江区,A 官网公示,\n",
                list_name="临时测试名单（第三方调研）", grade="site_curated",
                source_key="tmp_list", source_file="tmp.csv", used_for="测试用",
            )["report"]["replaced"]
            and len(li.read_registry()["lists"]) == 1,
        )
        check(
            "dry_run 不写任何文件",
            li.import_list(
                text=csv_head + "巳公司,x.com,企业官网,0571-88888888,杭州市滨江区,A,\n",
                list_name="另一个（第三方调研）", grade="site_curated",
                source_key="dry_only", dry_run=True,
            )["report"]["dry_run"]
            and len(li.read_registry()["lists"]) == 1,
        )
    finally:
        settings.data_dir = original_data_dir

    # 注册表 → 骨架来源：文件名去重，同名以注册表的核实程度为准
    from app.seed import companies as be_companies  # noqa: E402

    check(
        "内置名单与注册表按文件名去重合并（同一文件只算一份）",
        dict(cli_companies.source_files())["cluster_reps.json"] == "site_curated"
        and dict(cli_companies.source_files()).get("cluster_reps.json")
        == dict(be_companies.source_files()).get("cluster_reps.json")
        and len(cli_companies.source_files()) == len(set(dict(cli_companies.source_files()))),
        cli_companies.source_files(),
    )

    check(
        "正式注册表里记的就是这份 387 行的清单",
        (lambda entry: entry and entry["rows"] == 387 and entry["source_file"].endswith("_副本.csv"))(
            next((e for e in li.read_registry()["lists"] if e["source_key"] == "cluster_reps"), None)
        ),
        li.read_registry()["lists"],
    )

    # ⑨ 「已在别的名单里」必须摘掉**本次要覆盖的那份名单**再数。
    # 导入是覆盖式的：不摘的话这份名单会和自己对上一次（实测 317 行、137 行
    # 全部自报命中），报告里那个数字就等于没信息。
    # 这里直接量骨架层的摘除效果 —— 覆盖同一份名单时不再自报命中，
    # 而它同时也出现在官方名单里的那几十家**仍然**算「别的名单」。
    reps_names = {
        row["name"] for row in json.loads((ROOT / "data" / "source" / "cluster_reps.json").read_text(encoding="utf-8"))
    }
    all_names = {c["name"] for c in be_companies.load_company_skeletons()}
    other_names = {c["name"] for c in be_companies.load_company_skeletons(exclude_source_key="cluster_reps")}
    check(
        "摘掉本次要覆盖的名单后，它自己的行不再算进「已在别的名单里」",
        reps_names <= all_names
        and 0 < len(reps_names - other_names) < len(reps_names)
        and len(reps_names & other_names) < len(reps_names),
        (len(reps_names), len(reps_names - other_names), len(reps_names & other_names)),
    )

    # 接口侧的 `_database_delta` 只是把上面那句接上去。它只问一次「库里现在有哪些
    # 企业名」，所以给个最小替身就能离线验，不必连库、也不必真的导入。
    from app.routers.admin_lists import _database_delta  # noqa: E402

    class _NamesOnlySession:
        """`_database_delta` 用到 session 的只有 `execute(select(name)).all()`。"""

        class _Rows:
            def __init__(self, rows):
                self._rows = rows

            def all(self):
                return self._rows

        def __init__(self, names):
            self._names = names

        def execute(self, _statement):
            return self._Rows([(name,) for name in self._names])
    delta = _database_delta(
        _NamesOnlySession(all_names),
        [{"name": name} for name in reps_names],
        True,
        "cluster_reps",
    )
    check(
        "导入报告的「已在别的名单里」同样摘掉了这份名单自己",
        delta["also_in_other_lists"] == len(reps_names & other_names)
        and delta["also_in_other_lists"] < len(reps_names)
        and delta["new_to_db"] == 0
        and delta["dropped_from_db"] == [],
        delta,
    )

    # ------------------------------------------------------------------
    # CLI：`seed --csv` —— 让新放进 docs/ 的 CSV 能被命令行直接导入
    #
    # 在此之前这份 CLI 只能从 data/source/*.json 灌库，一份新 CSV 必须先进网页端
    # 或另写一个 tools/parse_*.py 才会变成产物，`seed --drop` 对它毫无反应 ——
    # 于是「照着文档跑完，新名单里的公司一家都没进库」看起来像个 bug，
    # 而报告里一个字都不提。下面四条钉住的就是这条路。
    # ------------------------------------------------------------------
    print("\n=== CLI：seed --csv ===")
    import contextlib
    import io

    from app import cli as app_cli  # noqa: E402

    repo_root = ROOT

    # ① 路径解析：文档里那条命令在 backend/ 下执行，而路径写的是相对仓库根的
    #    （docs/x.csv）。三种写法都要能落到同一个文件。
    csv_rel = "docs/全国气动元件企业调研清单.csv"
    resolved = app_cli.resolve_csv(csv_rel)
    check(
        "resolve_csv 认仓库根相对路径（在 backend/ 下也能用）",
        resolved == (repo_root / csv_rel).resolve(),
        str(resolved),
    )
    original_cwd = Path.cwd()
    try:
        import os

        os.chdir(repo_root / "backend")
        check(
            "resolve_csv 在 backend/ 目录下同样解得开 docs/ 路径",
            app_cli.resolve_csv(csv_rel) == (repo_root / csv_rel).resolve(),
            "",
        )
    finally:
        os.chdir(original_cwd)

    # ② 已登记的名单只给文件就够 —— 元数据从注册表读，不必把当初那堆参数再抄一遍
    #    （抄错一个字就是另一个 source_key，同一份名单在库里出现两条）。
    known = app_cli.registry_entry_for(resolved)
    check(
        "按文件名就能从注册表认出这份 CSV（含 source_key 与核实程度）",
        known.get("source_key") == "pneumatic_research"
        and known.get("grade") == "site_curated"
        and known.get("list_name"),
        {k: known.get(k) for k in ("source_key", "grade", "list_name")},
    )

    # ③ 没登记过、又没给 --source-key：必须**停下并说清怎么补**，不能自己编一个键。
    #    source_key 是产物文件名 + 注册表唯一键，猜错就是一条再也对不上的名单。
    def run_cli(argv):
        out = io.StringIO()
        try:
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
                app_cli.main(argv)
        except SystemExit as exc:
            return str(exc.code or ""), out.getvalue()
        return "", out.getvalue()

    # 用一份确定未登记的文件名来验（已登记的那几份会走 ② 的路径，覆盖不到这里）
    unregistered = ROOT / "data" / "source" / "__not_a_list__.csv"
    unregistered.write_text("公司名称,官网\n测试公司,\n", encoding="utf-8")
    try:
        code2, _ = run_cli(["seed", "--csv", str(unregistered), "--dry-run"])
    finally:
        unregistered.unlink()
    check(
        "未登记且没给 --source-key 时停下并给出补法（不自己编键）",
        "还没登记过" in code2 and "--source-key" in code2,
        code2[:80],
    )

    # ④ 多份 --csv 配一条元数据 = 会把它们全写进同一条注册表记录，必须拦住
    code3, _ = run_cli(
        [
            "seed",
            "--csv",
            csv_rel,
            "--csv",
            "docs/浙江省产业带企业调研清单.csv",
            "--source-key",
            "whatever",
            "--dry-run",
        ]
    )
    check(
        "多份 --csv 配一条元数据会被拦住（否则两份挤进同一记录）",
        "只能来自注册表" in code3 or "同一条注册表记录" in code3,
        code3[:90],
    )

    # ⑤ 注册表里的 source_file 必须**真的在磁盘上**。
    #    曾经有两条只写了文件名（没有 docs/ 前缀），来源地图上看着有值、
    #    实际指不到任何文件；顺着它去找源 CSV 的人会找不到。
    from app.services.list_import import imported_lists  # noqa: E402

    dangling = [e["source_key"] for e in imported_lists() if not (ROOT / e.get("source_file", "")).is_file()]
    check("注册表里每条 source_file 都指向真实存在的文件", not dangling, dangling)

    # ⑥ 每份已登记名单的「产业带 / 块状经济」取值都要能落位，否则那几十行
    #    会静默地不出现在任何集群页上（这个库因此丢过两次数据：355 行、1069 行）。
    #    这一条**按 CSV 实测**，不是查注册表 —— 查注册表只能证明导入过。
    from app.services.list_import import cluster_slug_index  # noqa: E402

    # 判据必须与灌库时**同一个函数**（``dispatch_cluster``）：它先查等价名
    # （CLUSTER_SYNONYMS，「塘下汽摩配」→「瑞安汽摩配」），再查骨架与派生档案。
    # 只查 index 会把 4 个别名误报成「缺档案」。
    #
    # 两份**刻意没有档案**的跨产区企业池（气动的「国产气动龙头」「外资品牌在华」）
    # 是有意为之，写进白名单 —— 白名单只有这两个，新增任何一条都要有人解释。
    INTENTIONAL_POOLS = {"国产气动龙头", "外资品牌在华"}
    index = cluster_slug_index()
    unresolved: dict[str, list[str]] = {}
    for entry in imported_lists():
        path = ROOT / entry.get("source_file", "")
        if not path.is_file():
            continue
        with path.open(encoding="utf-8-sig") as handle:
            header = next(csv.reader(handle), [])
            handle.seek(0)
            column = next((c for c in ("产业带", "块状经济") if c in header), "")
            if not column:
                continue
            names = {row[column].strip() for row in csv.DictReader(handle) if row.get(column, "").strip()}
        missing = sorted(n for n in names if not li.dispatch_cluster(n, index))
        if missing:
            unresolved[entry["source_key"]] = missing
    check(
        "每份名单的「产业带 / 块状经济」取值都有对应档案（含集装箱房 46 条）",
        set().union(*unresolved.values()) <= INTENTIONAL_POOLS if unresolved else True,
        {k: v[:4] for k, v in unresolved.items()},
    )

    print("\n=== 摘要联系方式抽取 ===")
    block = "杭州微慕科技有限公司 - 顺企网 www.11467.com 电话：0571-8899 1234 地址：浙江省杭州市余杭区文一西路998号 主营：锂电池"
    got = dc.extract_from_blocks("杭州微慕科技有限公司", [block])
    check("分段书写的区号号码能识别", got["public_phone"] == "0571-88991234", got["public_phone"])
    check("地址能抽出且截断到门牌", got["address"] == "浙江省杭州市余杭区文一西路998号", got["address"])

    got = dc.extract_from_blocks("杭州某某科技有限公司", ["杭州某某科技有限公司 联系人：张先生 手机：13812345678 地址：杭州市西湖区文三路100号"])
    check("手机号一律不采集", got["public_phone"] == "", got["public_phone"])

    got = dc.extract_from_blocks("仙鹤股份有限公司", ["仙鹤股份有限公司 电话：0599-1234567 地址：浙江省衢州市"])
    check("外省区号在浙江企业层不采信", got["public_phone"] == "", got["public_phone"])
    check("但没有电话不影响地址采集", got["address"] == "浙江省衢州市", got["address"])

    got = dc.extract_from_blocks("乐清昌德成电子有限公司", ["温州某某电气有限公司 电话：0577-6666 6666 地址：浙江省温州市"])
    check(
        "摘要没提到该企业 → 什么都不采",
        got == {"public_phone": "", "public_email": "", "address": "", "source_url": ""},
        got,
    )

    got = dc.extract_from_blocks("某订单号测试有限公司", ["某订单号测试有限公司 订单号：05718899123456789 地址：浙江省杭州市"])
    check("长数字串不得截出电话", got["public_phone"] == "", got["public_phone"])

    got = dc.extract_from_blocks("邮箱测试有限公司", ["邮箱测试有限公司 邮箱：sales@example.com 联系邮箱：info@mouqi.com 地址：浙江省杭州市滨江区"])
    check("占位邮箱被拒绝，取下一个", got["public_email"] == "info@mouqi.com", got["public_email"])

    rows = [
        {"name": "万沙电气有限公司", "public_phone": "400-000-9999"},
        {"name": "上上德盛集团股份有限公司", "public_phone": "400-000-9999"},
        {"name": "中科环力股份有限公司", "public_phone": "400-000-9999"},
        {"name": "独一家有限公司", "public_phone": "0571-88889999"},
    ]
    dc.drop_boilerplate(rows, 3)
    check("跨企业重复的聚合站号码被丢弃", all(not r["public_phone"] for r in rows[:3]))
    check("只出现在一家企业的号码保留", rows[3]["public_phone"] == "0571-88889999")

    # ------------------------------------------------------------------
    # 对外文案里不出现「抓取 / 爬虫 / 爬取」
    #
    # 这一条**扫源码**而不是扫接口返回：接口只能盖住后端生成的那几处，
    # 而文字也可能写死在前端模板里（上一轮就是这样漏掉了 Admin 页与 api.js）。
    # 注释里说明「为什么不用这个词」是允许的，所以只排查代码行里的字符串字面量。
    # ------------------------------------------------------------------
    print("\n=== 对外文案：不提「抓取 / 爬虫」 ===")
    forbidden = ("抓取", "爬虫", "爬取")
    offenders: list[str] = []
    roots = [ROOT / "frontend" / "src", ROOT / "backend" / "app"]
    for root in roots:
        for path in root.rglob("*"):
            if path.suffix not in (".vue", ".js", ".py") or "__pycache__" in path.parts:
                continue
            for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                stripped = line.lstrip()
                # 注释行放过：代码里写清「为什么改词」正是我们要留的信息
                if stripped.startswith(("#", "//", "*", "/*")):
                    continue
                if any(word in line for word in forbidden):
                    offenders.append(f"{path.relative_to(ROOT)}:{number}")
    check(
        "前端模板与后端对外文案里没有「抓取 / 爬虫 / 爬取」",
        not offenders,
        offenders[:8],
    )
    # 页脚备案栏是法定展示位，只放备案要素；「收录纪律与退出边界」入口已按站方要求移除。
    app_vue = (ROOT / "frontend" / "src" / "App.vue").read_text(encoding="utf-8")
    beian_block = app_vue.split('class="footer-beian"', 1)[-1].split("</footer>", 1)[0]
    check(
        "页脚备案栏不再挂「收录纪律与退出边界」入口",
        "收录纪律与退出边界" not in beian_block,
        [line.strip() for line in beian_block.splitlines() if "收录纪律" in line],
    )
    check(
        "那段声明仍可从页脚「关于我们」列到达",
        "about-us" in app_vue,
    )

    # ------------------------------------------------------------------
    # 「我们提供的服务」与「收录不收费、不卖排名」必须同时在场
    #
    # 这是全站唯一一处「主办单位对外业务」与「本站收录纪律」挨在一起的地方，
    # 也是最容易被后来的人改坏的一处：只删掉边界声明、或只删掉服务一节，
    # 都会留下一个说不通的页面（前者像是默认了付费能进名录，
    # 后者像是把已经对外公布的服务藏了起来）。
    # ------------------------------------------------------------------
    company_vue = (ROOT / "frontend" / "src" / "views" / "Company.vue").read_text(encoding="utf-8")
    site_js = (ROOT / "frontend" / "src" / "site.js").read_text(encoding="utf-8")
    for service in ("品牌优化", "出海跨境独立站建设", "长尾红人推广", "海外 B2B 获客"):
        check(f"服务名称逐字取自对外原文：{service}", service in site_js, "")
    check(
        "服务一节渲染的是 SERVICES（页面里不另写一份名称）",
        "v-for=\"service in SERVICES\"" in company_vue and "品牌优化" not in company_vue,
        [line.strip() for line in company_vue.splitlines() if "品牌优化" in line],
    )
    check(
        "服务一节同时说明「与收录、排序无关」并指向收录纪律",
        "这些服务与本站的收录、排序无关" in company_vue and "#policy" in company_vue,
        "",
    )
    check(
        "「不收入驻费、不卖排名」的声明没有被服务一节顶掉",
        "不收入驻费、不卖排名、不接受竞价、不做付费认证" in company_vue,
        "",
    )

    # ------------------------------------------------------------------
    # 导航与页脚的信息分工
    #
    # 「数据来源与核实程度」是**说明性**页面，跟「关于本站」同属一类，
    # 所以归页脚的「关于我们」列；顶部导航只留内容入口（产业地图 / 产业集群 /
    # 企业名录 / 关于我们）。这两件事是联动的：只改一处，页面上就会出现
    # 两个「关于我们」类入口，或者这个页面从导航里彻底消失。
    # ------------------------------------------------------------------
    nav_block = app_vue.split('class="nav"', 1)[-1].split("</nav>", 1)[0]
    check(
        "顶部导航不再重复挂「数据来源」",
        "{ name: 'about' }" not in nav_block,
        [line.strip() for line in nav_block.splitlines() if "/about" in line or "about'" in line],
    )
    about_col = app_vue.split("<h4>关于我们</h4>", 1)[-1].split("</div>", 1)[0]
    check(
        "「数据来源与核实程度」挂在页脚「关于我们」列，且排在「关于本站」之后",
        "{ name: 'about' }" in about_col
        and about_col.index("关于本站") < about_col.index("数据来源与核实程度"),
        [line.strip() for line in about_col.splitlines() if "RouterLink" in line],
    )

    # /about「名单口径声明」表此前把每一行都写死成 grade-official +「官方认定」，
    # 可九份名单里七份是第三方调研清单、一份是政府公开数据 ——
    # 那等于把调研方的结论说成官方认定，正是这个站最不能出的错。
    # 档位只能由接口给的 grade 决定，所以视图里不该再手写档位类名。
    # （注释里讲清这段历史是允许的，所以先去掉 HTML 注释再查。）
    about_vue = (ROOT / "frontend" / "src" / "views" / "About.vue").read_text(encoding="utf-8")
    about_code = re.sub(r"<!--.*?-->", "", about_vue, flags=re.S)
    check(
        "名单口径声明的档位标签取自 GRADE_CLASS，不是手写的 grade-official",
        "grade-official" not in about_code,
        [line.strip() for line in about_code.splitlines() if "grade-official" in line],
    )
    check(
        "名单口径声明的文字标签取自接口的 grade_label",
        "GRADE_CLASS[entry.grade]" in about_code,
    )

    # ------------------------------------------------------------------
    # /map 不再展示「按县汇总的家数」
    #
    # 那个数字（`province.enterprise_count`）比首页的收录总数小一截，摆在数字带第一位
    # 时读者会拿它当总量、以为企业少了一批。中间一版改成并排解释，
    # 站方看过之后要求撤掉 —— 它在页面上没有用处（既不是收录量，也当不了覆盖率分子）。
    # 这条断言守的是「别再放回去」：数字带里不许出现 `province.enterprise_count`。
    # ------------------------------------------------------------------
    map_view = (ROOT / "frontend" / "src" / "views" / "MapView.vue").read_text(encoding="utf-8")
    kpi_block = map_view.split('class="kpi-band"', 1)[-1].split("</div>\n    </section>", 1)[0]
    # 注释里说明「为什么撤掉」是我们想留的信息（本文件顶部就写着一段），所以只查渲染出来的文字
    map_markup = re.sub(r"<!--.*?-->", "", map_view, flags=re.S)
    map_markup = "\n".join(
        line for line in map_markup.splitlines() if not line.lstrip().startswith("//")
    )
    check(
        "地图数字带不再展示「按县汇总的家数」",
        "province.enterprise_count" not in kpi_block and "按县汇总" not in map_markup,
        [line.strip() for line in kpi_block.splitlines() if "enterprise_count" in line]
        or [line.strip() for line in map_markup.splitlines() if "按县汇总" in line],
    )

    print(f"\n=== {passed} passed, {failed} failed ===")
    if failures:
        print("失败项：")
        for name in failures:
            print("  -", name)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
