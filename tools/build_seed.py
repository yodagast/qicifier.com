"""Build the curated reference seed: 415X tracks + town-level 块状经济 clusters.

Data grades (see docs/浙江产业集群收录体系搭建方案.html §05):

* ``official``      — 415X 一级分类. Track names are taken verbatim from the
  official 《2026年浙江制造精品推广目录》公示名单 (docs/产业群名单.pdf), which
  labels every record with its “415X” 集群赛道.
* ``site_curated``  — town-level 块状经济. Zhejiang's most valuable industrial
  belts are named after 镇/街道 and have **no** unified official list, so they are
  recorded as 站方整理 and must be labelled as such wherever they are displayed.

Output: data/curated/clusters_seed.json
"""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "curated" / "clusters_seed.json"
MINGPIN = ROOT / "data" / "source" / "zhejiang_mingpin_2026.json"

# 415X 15 个千亿级集群赛道 → 所属板块。赛道名一字不改地取自公示名单原文。
# 板块归类为站方整理（对应 415X 体系的「4 大世界级集群」分组）。
TRACK_SECTORS = {
    "智能物联": "数字产业",
    "人工智能": "数字产业",
    "集成电路": "数字产业",
    "高端软件": "数字产业",
    "工业母机与机器人": "高端装备",
    "电气与农机装备": "高端装备",
    "高端船舶与海工装备": "高端装备",
    "低空经济与航空航天": "高端装备",
    "新能源汽车及零部件": "高端装备",
    "现代家居与智能家电": "现代消费与健康",
    "现代纺织与服装": "现代消费与健康",
    "生物医药与医疗器械": "现代消费与健康",
    "高端新材料": "绿色石化与新材料",
    "新能源与新型储能": "绿色石化与新材料",
    "绿色石化": "绿色石化与新材料",
}

# 镇街级块状经济（站方整理）:
# slug, 名称, 市, 县(市/区), 镇/街道, 二级细分, 核心产区说明
TOWN_CLUSTERS = [
    ("datang-waye", "大唐袜业", "绍兴市", "诸暨市", "大唐街道", "袜业", "大唐街道（原大唐镇）"),
    ("puyuan-maoshan", "濮院毛衫", "嘉兴市", "桐乡市", "濮院镇", "毛衫针织", "濮院镇"),
    ("liushi-dianqi", "柳市低压电器", "温州市", "乐清市", "柳市镇", "低压电器", "柳市镇"),
    ("zhili-tongzhuang", "织里童装", "湖州市", "吴兴区", "织里镇", "童装", "织里镇"),
    ("diankou-wujin", "店口五金", "绍兴市", "诸暨市", "店口镇", "铜加工与五金", "店口镇"),
    ("shanxiahu-zhenzhu", "山下湖珍珠", "绍兴市", "诸暨市", "山下湖镇", "珍珠养殖与加工", "山下湖镇"),
    ("fenshui-zhibi", "分水制笔", "杭州市", "桐庐县", "分水镇", "制笔", "分水镇"),
    ("hengdian-cailiao", "横店磁性材料", "金华市", "东阳市", "横店镇", "磁性材料", "横店镇"),
    ("yiwu-xiaoshangpin", "义乌小商品", "金华市", "义乌市", "稠城街道", "小商品集散", "义乌市"),
    ("keqiao-fangzhi", "柯桥纺织", "绍兴市", "柯桥区", "柯桥街道", "纺织印染", "柯桥区"),
    ("haining-pige", "海宁皮革", "嘉兴市", "海宁市", "海州街道", "皮革服装", "海宁市"),
    ("pinghu-guangjidian", "平湖光机电", "嘉兴市", "平湖市", "当湖街道", "光机电", "平湖市"),
    ("yongkang-wujin", "永康五金", "金华市", "永康市", "东城街道", "五金制品", "永康市"),
    ("wenzhou-xiege", "温州鞋革", "温州市", "鹿城区", "双屿街道", "鞋革", "温州城区"),
    ("ruian-qimopei", "瑞安汽摩配", "温州市", "瑞安市", "塘下镇", "汽车摩托车配件", "瑞安市"),
    ("yuhuan-famen", "玉环水暖阀门", "台州市", "玉环市", "玉城街道", "水暖阀门", "玉环市"),
    ("wenling-bengdianji", "温岭泵与电机", "台州市", "温岭市", "大溪镇", "泵与电机", "温岭市"),
    ("huangyan-moju", "黄岩模具", "台州市", "黄岩区", "西城街道", "塑料模具", "黄岩区"),
    ("linhai-huwaipinyong", "临海户外休闲用品", "台州市", "临海市", "古城街道", "户外休闲用品", "临海市"),
    ("anji-yiye", "安吉椅业", "湖州市", "安吉县", "递铺街道", "办公椅", "安吉县"),
    ("changxing-xudianchi", "长兴蓄电池", "湖州市", "长兴县", "画溪街道", "蓄电池", "长兴县"),
    ("deqing-shengwu", "德清生物医药", "湖州市", "德清县", "武康街道", "生物医药", "德清县"),
    ("tongxiang-huaxian", "桐乡化纤", "嘉兴市", "桐乡市", "梧桐街道", "化纤", "桐乡市"),
    ("jiashan-muye", "嘉善木业家具", "嘉兴市", "嘉善县", "魏塘街道", "木业家具", "嘉善县"),
    ("haiyan-jingujian", "海盐紧固件", "嘉兴市", "海盐县", "武原街道", "紧固件", "海盐县"),
    ("yuyao-suliao", "余姚塑料模具", "宁波市", "余姚市", "凤山街道", "塑料模具", "余姚市"),
    ("cixi-jiadian", "慈溪家电", "宁波市", "慈溪市", "浒山街道", "小家电", "慈溪市"),
    ("ninghai-wenju", "宁海文具", "宁波市", "宁海县", "跃龙街道", "文具", "宁海县"),
    ("xiangshan-zhenzhi", "象山针织", "宁波市", "象山县", "丹东街道", "针织服装", "象山县"),
    ("fenghua-qidong", "奉化气动元件", "宁波市", "奉化区", "锦屏街道", "气动元件", "奉化区"),
    ("shengzhou-lingdai", "嵊州领带", "绍兴市", "嵊州市", "三江街道", "领带服饰", "嵊州市"),
    ("xinchang-zhoucheng", "新昌轴承", "绍兴市", "新昌县", "南明街道", "轴承", "新昌县"),
    ("shangyu-sanye", "上虞伞业", "绍兴市", "上虞区", "崧厦街道", "伞业", "崧厦街道"),
    ("longquan-qingci", "龙泉青瓷宝剑", "丽水市", "龙泉市", "龙渊街道", "青瓷宝剑", "龙泉市"),
    ("qingtian-shidiao", "青田石雕", "丽水市", "青田县", "鹤城街道", "石雕", "青田县"),
    ("yunhe-muzhiwanju", "云和木制玩具", "丽水市", "云和县", "浮云街道", "木制玩具", "云和县"),
    ("jinyun-daijuchuang", "缙云带锯床", "丽水市", "缙云县", "五云街道", "带锯床与机床", "缙云县"),
    ("songyang-chaye", "松阳茶叶", "丽水市", "松阳县", "西屏街道", "茶叶", "松阳县"),
    ("suichang-jinshu", "遂昌金属制品", "丽水市", "遂昌县", "妙高街道", "金属制品", "遂昌县"),
    ("jiangshan-mumen", "江山木门", "衢州市", "江山市", "虎山街道", "木门", "江山市"),
    ("longyou-tezhongzhi", "龙游特种纸", "衢州市", "龙游县", "东华街道", "特种纸", "龙游县"),
    ("lanxi-fangzhi", "兰溪纺织", "金华市", "兰溪市", "兰江街道", "棉纺织", "兰溪市"),
    ("pujiang-shuijing", "浦江水晶", "金华市", "浦江县", "浦阳街道", "水晶工艺", "浦江县"),
    ("wuyi-diandonggongju", "武义电动工具", "金华市", "武义县", "壶山街道", "电动工具", "武义县"),
    ("panan-zhongyaocai", "磐安中药材", "金华市", "磐安县", "安文街道", "中药材", "磐安县"),
    ("dongyang-mudiao", "东阳木雕红木", "金华市", "东阳市", "吴宁街道", "木雕红木", "东阳市"),
    ("putuo-chuanbo", "普陀船舶修造", "舟山市", "普陀区", "沈家门街道", "船舶修造", "普陀区"),
    # ── 第五份清单（浙江省镇街级块状经济企业清单）带来的新增产区 ──
    # 收录判据与前 47 条不同：这条的来源是一份**具体的企业清单**，
    # 每条至少挂了 5-7 家企业，所以不再是「有空壳风险的站方线索」。
    ("xiaoshan-huaxian", "衙前化纤", "杭州市", "萧山区", "衙前镇", "化纤", "衙前镇"),
    ("xiaoshan-nanyang-san", "南阳制伞", "杭州市", "萧山区", "南阳街道", "制伞", "南阳街道"),
    ("linan-dianxian", "临安电线电缆", "杭州市", "临安区", "玲珑街道", "电线电缆", "玲珑街道（主）"),
    ("cixi-zhouxiang", "周巷小家电", "宁波市", "慈溪市", "周巷镇", "小家电", "周巷镇"),
    ("cixi-guanhaiwei", "观海卫电源连接器", "宁波市", "慈溪市", "观海卫镇", "电源连接器与插座", "观海卫镇"),
    ("xiangshan-juexi", "爵溪针织", "宁波市", "象山县", "爵溪街道", "针织", "爵溪街道"),
    ("yuyao-moju-dengju", "余姚模具与灯具", "宁波市", "余姚市", "梁弄镇", "塑料模具与灯具", "梁弄镇 / 余姚模具城"),
    ("yongjia-oubei", "瓯北泵阀", "温州市", "永嘉县", "瓯北街道", "泵阀", "瓯北街道"),
    ("ouhai-yanjing", "瓯海眼镜", "温州市", "瓯海区", "娄桥街道", "眼镜", "娄桥街道"),
    ("pingyang-xiaojiang", "萧江塑编", "温州市", "平阳县", "萧江镇", "塑料编织", "萧江镇"),
    ("yueqing-hongqiao", "虹桥电子元器件", "温州市", "乐清市", "虹桥镇", "电子元器件", "虹桥镇"),
    ("tongxiang-zhouquan", "洲泉化纤", "嘉兴市", "桐乡市", "洲泉镇", "化纤", "洲泉镇"),
    ("haining-xucun", "许村家纺", "嘉兴市", "海宁市", "许村镇", "家纺布艺", "许村镇"),
    ("pinghu-xiangbao", "平湖箱包", "嘉兴市", "平湖市", "新埭镇", "旅行箱包", "新埭镇 / 钟埭街道"),
    ("nanxun-mudiban", "南浔木地板", "湖州市", "南浔区", "南浔镇", "木地板", "南浔镇 / 练市镇"),
    ("yiwu-dachen", "大陈衬衫", "金华市", "义乌市", "大陈镇", "衬衫", "大陈镇"),
    ("lanxi-mianfangzhi", "兰溪棉纺织", "金华市", "兰溪市", "永昌街道", "棉纺织织造", "经济开发区 / 永昌街道"),
    ("changshan-zhoucheng", "常山轴承", "衢州市", "常山县", "辉埠镇", "轴承", "辉埠镇 / 白石镇"),
    ("jiangshan-yumaoqiu", "江山羽毛球", "衢州市", "江山市", "虎山街道", "文体用品", "虎山街道 / 贺村镇"),
    ("jiaojiang-xiahchen", "下陈缝纫机", "台州市", "椒江区", "下陈街道", "缝纫机与缝制设备", "下陈街道"),
    ("linhai-duqiao", "杜桥眼镜", "台州市", "临海市", "杜桥镇", "眼镜", "杜桥镇"),
    ("tiantai-tantou", "坦头汽车用品", "台州市", "天台县", "坦头镇", "汽车用品", "坦头镇"),
    ("sanmen-chongfengyi", "三门冲锋衣", "台州市", "三门县", "海润街道", "户外服装", "海润街道 / 海游街道"),
    ("yuhuan-famen-shuinuan", "玉环阀门水暖", "台州市", "玉环市", "龙溪镇", "阀门与水暖", "龙溪镇 / 清港镇 / 玉城街道"),
    ("qingyuan-qianbi", "庆元铅笔", "丽水市", "庆元县", "竹口镇", "铅笔", "竹口镇 / 屏都街道"),
    ("jinyun-daijuchuang-hu", "缙云壶镇带锯床", "丽水市", "缙云县", "壶镇镇", "带锯床", "壶镇镇"),
    ("songyang-buxiugang", "松阳不锈钢管", "丽水市", "松阳县", "西屏街道", "不锈钢管", "西屏街道 / 赤寿乡"),
    ("dinghai-jintang", "金塘螺杆", "舟山市", "定海区", "金塘镇", "塑料机械螺杆", "金塘镇"),
    ("putuo-shuichan", "普陀水产加工", "舟山市", "普陀区", "沈家门街道", "水产加工", "沈家门街道"),
    # ── 第六份清单（临安区规上企业基本信息，政府公开数据）带来的新增产区 ──
    # 这份的来源与前几份都不同：它是一张**政府公开的企业底表**（594 家规上企业，
    # 只有街道/行业，没有电话、没有地址），产区是从「所属街道 × 产业名称」的
    # 交叉表里读出来的。判据更硬，但产区本身仍属站方整理，故档位不变。
    #
    # 前六条是**有称号、有主镇街**的真块状经济（「中国电缆之乡」等）；
    # 后七条是区级产业分组（临安新材料、临安纺织服装…），它们的产业确实
    # 横跨多个镇街，按「多镇街产区只登记主镇街」的规则取企业数最多的那个
    # 镇街建档，范围写进 core_area。第七条起不再单列，是因为临安的规上企业
    # 表只到区级，再往下拆就变成猜。
    # 「临安电线电缆」上一份清单已经建过档（linan-dianxian），此处不重复登记。
    ("linan-zhuangshizhi", "临安装饰纸", "杭州市", "临安区", "玲珑街道", "装饰纸", "玲珑街道（主）/ 於潜镇 / 太湖源镇 / 板桥镇"),
    ("linan-shanhetao", "临安山核桃", "杭州市", "临安区", "龙岗镇", "坚果炒货", "龙岗镇（主）/ 清凉峰镇 / 岛石镇"),
    ("linan-jienengdeng", "临安节能灯", "杭州市", "临安区", "高虹镇", "照明电器", "高虹镇（主）/ 太湖源镇"),
    ("linan-taiyang-wujin", "太阳五金工具", "杭州市", "临安区", "太阳镇", "五金工具", "太阳镇（主）/ 於潜镇"),
    ("qingshanhu-zhuangbei", "青山湖科技城高端装备", "杭州市", "临安区", "青山湖街道", "高端装备", "青山湖街道（青山湖科技城）"),
    ("linan-xincailiao", "临安新材料", "杭州市", "临安区", "青山湖街道", "新材料", "青山湖街道 / 於潜镇 / 太湖源镇 / 玲珑街道"),
    ("linan-fangzhifuzhuang", "临安纺织服装", "杭州市", "临安区", "太湖源镇", "纺织服装", "太湖源镇（主）/ 玲珑街道 / 天目山镇"),
    ("linan-dianzidianlu", "临安电子电路", "杭州市", "临安区", "玲珑街道", "电子电路", "玲珑街道（主）/ 锦南街道 / 高虹镇"),
    ("linan-shengwuyiyao", "临安生物医药", "杭州市", "临安区", "青山湖街道", "生物医药", "青山湖街道 / 锦南街道"),
    ("linan-xinnengyuan", "临安新能源", "杭州市", "临安区", "青山湖街道", "新能源", "青山湖街道 / 锦南街道"),
    ("linan-jichengdianlu", "临安集成电路", "杭州市", "临安区", "青山湖街道", "集成电路", "青山湖街道（青山湖科技城）"),
    ("linan-jingmiyuanqijian", "临安精密元器件", "杭州市", "临安区", "昌化镇", "精密元器件", "昌化镇"),
]


def observed_tracks() -> list[str]:
    """415X track names actually printed in the official 公示名单."""
    if MINGPIN.exists():
        rows = json.loads(MINGPIN.read_text(encoding="utf-8"))
        seen = [r["track"] for r in rows if r.get("track")]
        return list(dict.fromkeys(seen))
    return list(TRACK_SECTORS)


def build() -> dict:
    tracks = []
    for name in observed_tracks():
        tracks.append(
            {
                "slug": "415X-" + name,
                "name": name,
                "industry_l1": name,
                "industry_l2": "",
                "sector": TRACK_SECTORS.get(name, ""),
                "scale_tier": "",
                "tier": "A",
                "data_grade": "official",
                "tier_note": (
                    "“415X” 集群赛道，名称取自《2026年浙江制造精品推广目录》公示名单原文；"
                    "本目录只登记在该名单中有据可查的赛道"
                ),
            }
        )

    clusters = [
        {
            "slug": slug,
            "name": name,
            "city": city,
            "district": district,
            "town": town,
            "industry_l2": industry_l2,
            "core_area": core_area,
            "scale_tier": "块状经济",
            "tier": "C",
            "data_grade": "site_curated",
            "honors": [],
            "note": "镇街级块状经济，无统一官方名单，属站方整理，未逐条核验",
        }
        for slug, name, city, district, town, industry_l2, core_area in TOWN_CLUSTERS
    ]

    return {
        "_note": (
            "415X 顶层分类为官方口径；镇街级块状经济为站方整理（site_curated），"
            "首次上线前需按调研方案 07 节流程逐条核验，不得标为官方来源。"
        ),
        "_sources": [
            {
                "name": "《浙江省“415X”先进制造业集群建设行动方案》（2023-01 印发）",
                "grade": "official",
                "used_for": "415X 顶层产业分类",
            },
            {
                "name": "docs/产业群名单.pdf —《2026年浙江制造精品推广目录》公示名单",
                "grade": "official",
                "used_for": "415X 集群赛道名称（官方原文）",
            },
        ],
        "tracks": tracks,
        "clusters": clusters,
    }


def main() -> None:
    payload = build()
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"tracks={len(payload['tracks'])} clusters={len(payload['clusters'])} -> {OUT}")


if __name__ == "__main__":
    main()