"""端到端回归测试：账号、收藏、以及数据完整性约束。

需要一个正在运行的后端（默认 http://127.0.0.1:5172）。

    python tests/test_api.py
    BASE=http://127.0.0.1:5172 python tests/test_api.py

设计原则：只断言**必须成立**的约束（安全边界、数据一致性），
不断言会随数据增长而变化的绝对数量（总数改为下限/区间断言）。
"""

from __future__ import annotations

import json
import mimetypes
import os
import re
import sys
import urllib.error
import urllib.request
import uuid
from pathlib import Path
from urllib.parse import quote

BASE = os.environ.get("BASE", "http://127.0.0.1:5172")
ROOT = Path(__file__).resolve().parents[1]

# 医疗器械清单里 9 个产区的 slug 与**集群当前**成员家数。
# 写死是有意的：这份清单的产区名与家数是**调研方给的**，变了就该有人看一眼。
#
# 注意这是「集群当前家数」，不是「这份清单自己的行数」——别的调研清单
# 也可以给同一条产区补企业（2026-10-21 的《浙江省产业带企业调研清单》
# 就让东阳从 8 家变成 10 家）。所以数字会随新名单增长，**增长本身不是错**，
# 但必须有人确认「多出来的那几家确实是新名单带来的」，而不是归属算错了。
MEDICAL_AREA_COUNTS = {
    "hangzhou-gaoduan-yiliaoqixie": 25,
    "ningbo-yiliaoqixie": 18,
    "tonglu-neikuijing": 12,
    "wenzhou-chuanci-shuzhu": 11,
    "huzhou-tiwaizhenduan": 11,
    "jiaxing-yiliaoqixie": 11,
    "taizhou-wujun-yiliaoqixie": 10,
    "shaoxing-yiyong-fuliao": 9,
    "dongyang-yiliaoqixie": 10,
}
MEDICAL_AREA_SLUGS = set(MEDICAL_AREA_COUNTS)

passed = failed = 0
failures: list[str] = []


def call(
    method: str, path: str, body=None, token: str | None = None, timeout: int = 25
) -> tuple[int, object]:
    data = json.dumps(body).encode() if body is not None else None
    # 路径里会有中文（集群 slug 带中文名），urllib 只接受 ASCII，先做百分号编码
    request = urllib.request.Request(BASE + quote(path, safe="/?&=:%"), data=data, method=method)
    if data:
        request.add_header("Content-Type", "application/json")
    if token:
        request.add_header("Authorization", f"Bearer {token}")
        if path.startswith("/api/admin"):
            request.add_header("X-Admin-Token", token)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = response.read().decode()
            return response.status, (json.loads(payload) if payload else None)
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode()
        try:
            return exc.code, json.loads(raw)
        except json.JSONDecodeError:
            return exc.code, raw


def call_multipart(
    path: str,
    file_path: Path,
    fields: dict,
    token: str | None = None,
    override_bytes: bytes | None = None,
) -> tuple[int, object]:
    """multipart/form-data 上传（名单导入接口收 CSV 文件）。"""
    boundary = "----zanzhu" + uuid.uuid4().hex
    body = bytearray()
    for key, value in fields.items():
        body += f"--{boundary}\r\n".encode()
        body += f'Content-Disposition: form-data; name="{key}"\r\n\r\n'.encode()
        body += f"{value}\r\n".encode()
    payload = override_bytes if override_bytes is not None else file_path.read_bytes()
    mime = mimetypes.guess_type(file_path.name)[0] or "text/csv"
    body += f"--{boundary}\r\n".encode()
    body += f'Content-Disposition: form-data; name="file"; filename="{file_path.name}"\r\n'.encode()
    body += f"Content-Type: {mime}\r\n\r\n".encode()
    body += payload + b"\r\n"
    body += f"--{boundary}--\r\n".encode()

    request = urllib.request.Request(
        BASE + quote(path, safe="/?&=:%"), data=bytes(body), method="POST"
    )
    request.add_header("Content-Type", f"multipart/form-data; boundary={boundary}")
    if token:
        request.add_header("X-Admin-Token", token)
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            return response.status, json.loads(response.read().decode() or "null")
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode()
        try:
            return exc.code, json.loads(raw)
        except json.JSONDecodeError:
            return exc.code, raw


def check(label: str, condition: bool, detail: str = "") -> None:
    global passed, failed
    if condition:
        passed += 1
        print(f"  PASS  {label} {detail}")
    else:
        failed += 1
        failures.append(label)
        print(f"  FAIL  {label} {detail}")


def section(title: str) -> None:
    print(f"\n=== {title} ===")


def main() -> int:
    # 管理接口的令牌来自环境变量；没导出就跳过需要它的往返用例
    admin_token = os.environ.get("ADMIN_TOKEN", "").strip()
    suffix = uuid.uuid4().hex[:8]
    username = f"test_{suffix}"
    password = "Zhejiang#2026"

    section("健康检查")
    status, health = call("GET", "/api/health")
    check("后端可访问", status == 200 and health.get("status") == "ok", health)
    if status != 200:
        print("\n后端未运行，跳过其余测试")
        return 1

    section("注册（无需验证）")
    status, data = call(
        "POST", "/api/account/register",
        {"username": username, "password": password, "display_name": "测试采购"},
    )
    check("注册成功并直接返回令牌", status == 201 and "token" in data, f"status={status}")
    token = data.get("token", "")

    status, _ = call("POST", "/api/account/register", {"username": username, "password": password})
    check("重复用户名被拒 (409)", status == 409, f"status={status}")

    for bad, why in (("12", "过短用户名"), ("含 空格", "非法字符")):
        status, _ = call("POST", "/api/account/register", {"username": bad, "password": password})
        check(f"非法用户名被拒：{why}", status == 422, f"status={status}")

    status, data = call("POST", "/api/account/register", {"username": f"weak_{suffix}", "password": "12345678"})
    check("弱密码被拒 (422)", status == 422 and "problems" in (data.get("detail") or {}), data)

    section("登录")
    status, data = call("POST", "/api/account/login", {"username": username, "password": password})
    check("正确密码可登录", status == 200 and "token" in data, f"status={status}")

    status, _ = call("POST", "/api/account/login", {"username": username, "password": "wrongpass1"})
    check("错误密码被拒 (401)", status == 401, f"status={status}")

    status, _ = call("POST", "/api/account/login", {"username": "no_such_user_xyz", "password": password})
    check("不存在的用户被拒 (401)", status == 401, f"status={status}")

    section("会话令牌")
    status, me = call("GET", "/api/account/me", token=token)
    check("带令牌可读到自己", status == 200 and me["username"] == username, me)

    status, _ = call("GET", "/api/account/me")
    check("无令牌被拒 (401)", status == 401, f"status={status}")

    status, _ = call("GET", "/api/account/me", token="forged.token")
    check("伪造令牌被拒 (401)", status == 401, f"status={status}")

    forged = "eyJ1aWQiOjk5OTk5OSwidSI6ImhhY2tlciIsImV4cCI6OTk5OTk5OTk5OX0.badsig"
    status, _ = call("GET", "/api/account/me", token=forged)
    check("篡改载荷的令牌被拒 (401)", status == 401, f"status={status}")

    section("收藏")
    status, page = call("GET", "/api/enterprises?page_size=2")
    check("企业列表可用", status == 200 and len(page["items"]) == 2, page.get("meta"))
    target = page["items"][0]["id"]
    other = page["items"][1]["id"]

    status, data = call("POST", "/api/account/favorites", {"enterprise_id": target, "note": "交期 45 天"}, token=token)
    check("可添加收藏", status == 201 and data["created"] is True, data)

    status, data = call("POST", "/api/account/favorites", {"enterprise_id": target}, token=token)
    check("重复收藏幂等", status == 201 and data["created"] is False, data)

    status, data = call("GET", "/api/account/favorites", token=token)
    check("收藏列表只含自己的记录", status == 200 and data["total"] == 1, data["total"])
    check(
        "收藏项带企业来源名单",
        bool(data["items"][0]["enterprise"]["list_names"]),
        data["items"][0]["enterprise"]["list_names"],
    )

    status, data = call("GET", "/api/account/favorites/ids", token=token)
    check("ids 接口返回已收藏 id", status == 200 and data["ids"] == [target], data)

    status, data = call("PATCH", f"/api/account/favorites/{target}", {"note": "已寄样"}, token=token)
    check("可更新备注", status == 200 and data["note"] == "已寄样", data)

    status, data = call("POST", "/api/account/favorites", {"enterprise_id": other}, token=token)
    check("可收藏多于一家的工厂", status == 201, data)

    status, _ = call("GET", "/api/account/favorites")
    check("未登录无法查看收藏 (401)", status == 401, f"status={status}")

    status, data = call("POST", "/api/account/register", {"username": f"other_{suffix}", "password": password})
    other_token = data["token"]
    status, data = call("GET", "/api/account/favorites", token=other_token)
    check("用户之间收藏相互隔离", status == 200 and data["total"] == 0, data["total"])

    status, _ = call("DELETE", f"/api/account/favorites/{target}", token=other_token)
    check("不能取消别人的收藏 (404)", status == 404, f"status={status}")

    status, data = call("DELETE", f"/api/account/favorites/{target}", token=token)
    check("可取消自己的收藏", status == 200 and data["removed"] is True, data)

    section("数据完整性")
    status, stats = call("GET", "/api/stats")
    check("企业总数 >= 616", stats["enterprise_total"] >= 616, stats["enterprise_total"])
    check("415X 赛道 = 15", stats["track_total"] == 15, stats["track_total"])

    status, tracks = call("GET", "/api/tracks")
    names = {t["name"] for t in tracks}
    for expected in ("低空经济与航空航天", "工业母机与机器人", "高端船舶与海工装备", "高端软件"):
        check(f"赛道名逐字取自原文：{expected}", expected in names, "")

    all_items = []
    for page_no in range(1, 4):
        _, more = call("GET", f"/api/clusters?page_size=100&page={page_no}")
        all_items += more["items"]
    newstar = [c for c in all_items if c["scale_tier"] == "新星产业群"]
    check("新星产业群 = 22", len(newstar) == 22, len(newstar))
    check(
        "新星产业群为 B 级且官方认定",
        all(c["tier"] == "B" and c["data_grade"] == "official" for c in newstar),
        "",
    )

    # ------------------------------------------------------------------
    # 集群列表排序
    #
    # 四种排序都必须**真的排了**（不是只接受参数）：判据取列表页上看得见的那两列
    # ——顺序是否单调、以及默认排序有没有被改动。默认值是最容易悄悄改坏的一处：
    # 老链接不传 sort 时得到的顺序必须与排序上线之前完全一致（tier, name）。
    # ------------------------------------------------------------------
    section("集群列表排序")
    def cluster_order(sort_key: str | None):
        url = "/api/clusters?page_size=100"
        if sort_key:
            url += f"&sort={sort_key}"
        _, page = call("GET", url)
        return page["items"]

    default_items = cluster_order(None)
    tier_items = cluster_order("tier")
    check(
        "不传 sort 与 sort=tier 顺序一致（默认排序没被改动）",
        [c["slug"] for c in default_items] == [c["slug"] for c in tier_items],
        [c["slug"] for c in default_items[:3]],
    )

    by_name = cluster_order("name")
    check(
        "按集群名排序：名字单调不减",
        [c["name"] for c in by_name] == sorted(c["name"] for c in by_name),
        [c["name"] for c in by_name[:3]],
    )

    by_count = cluster_order("count")
    counts = [c["enterprise_count"] for c in by_count]
    check(
        "按企业数排序：数字单调不增，且排在最前的是企业最多的那条",
        counts == sorted(counts, reverse=True) and counts[0] == max(counts),
        counts[:5],
    )

    by_industry = cluster_order("industry")
    # 「产业」列显示的是 l1 · l2 的拼接（415X 赛道只有 l1、调研产区只有 l2），
    # 排序键必须与它一致，否则会出现「按产业排出来的顺序看着不像按产业排」。
    labels = [c["industry_label"] for c in by_industry]
    check(
        "按产业排序：用与列表显示同一个串（l1 · l2），且单调不减",
        labels == sorted(labels) and any(c["industry_l1"] and not c["industry_l2"] for c in by_industry),
        labels[:5],
    )
    check(
        "「产业」串由后端给出（前端不再自己拼一遍）",
        all("industry_label" in c for c in by_industry)
        and all(
            c["industry_label"] == " · ".join(p for p in (c["industry_l1"], c["industry_l2"]) if p)
            for c in by_industry
        ),
        [c["industry_label"] for c in by_industry[:3]],
    )
    check(
        "非法 sort 取值被 422 挡住（不是静默回落到默认）",
        call("GET", "/api/clusters?sort=bogus")[0] == 422,
        call("GET", "/api/clusters?sort=bogus")[0],
    )

    status, regions = call("GET", "/api/regions")
    counties = [d for c in regions[0]["children"] for d in c["children"] if not d.get("is_zone")]
    zones = [d for c in regions[0]["children"] for d in c["children"] if d.get("is_zone")]
    check("国标县级行政区 = 90", len(counties) == 90, len(counties))
    check("省级新区/开发区单独标记", len(zones) >= 3, len(zones))

    section("联系方式边界（个人信息保护）")
    mobile = re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)")
    leaks = []
    for page_no in range(1, 4):
        _, data = call("GET", f"/api/enterprises?page_size=100&page={page_no}")
        for item in data["items"]:
            for field in ("public_phone", "public_email", "address"):
                if item.get(field) and mobile.search(str(item[field])):
                    leaks.append((item["name"], field, item[field]))
    check("接口未返回任何个人手机号", not leaks, leaks[:3])

    phone_shape = re.compile(r"^(?:400|800)-\d{3}-\d{4}$|^0[1-9]\d{1,2}-\d{7,8}(?:-\d{1,6})?$")
    bad_phones = []
    for page_no in range(1, 4):
        _, data = call("GET", f"/api/enterprises?page_size=100&page={page_no}")
        for item in data["items"]:
            if item.get("public_phone") and not phone_shape.match(item["public_phone"]):
                bad_phones.append((item["name"], item["public_phone"]))
    check("公开电话形态全部合法", not bad_phones, bad_phones[:3])

    section("第三方调研清单导入（武义户外运动与休闲）")
    status, listed = call("GET", "/api/enterprises?data_grade=site_curated&page_size=100")
    check(
        "第三方调研清单企业合计 1959 家（含现代纺织与服装 317 行、医疗器械 309 行、无缝 137 行修订版、"
        "415X 调研清单带进的 167 家、全国气动元件 460 家）",
        listed["meta"]["total"] == 1959,
        listed["meta"]["total"],
    )
    # 新增的两批代表企业里，第二批量最大；抽一家只出现在第二批的企业确认真的入库了
    _, sampled = call("GET", "/api/enterprises?q=%E7%81%B5%E4%BC%B4%E7%A7%91%E6%8A%80")
    check(
        "第二批代表企业已入库并带核实档位",
        sampled["meta"]["total"] >= 1
        and sampled["items"][0]["data_grade"] == "site_curated"
        and sampled["items"][0]["verify_tier"].startswith("A"),
        [(i["name"], i["verify_tier"]) for i in sampled["items"]],
    )
    check(
        "调研清单的核实程度是「站方整理」而不是官方认定",
        all(i["data_grade"] == "site_curated" and i["data_grade_label"] == "站方整理" for i in listed["items"]),
        {i["data_grade_label"] for i in listed["items"]},
    )
    check(
        "调研清单企业逐条带核实档位",
        all(i["verify_tier"] for i in listed["items"]),
        [i["name"] for i in listed["items"] if not i["verify_tier"]][:3],
    )
    # 不是每份清单都有「主营产品」列：第四批代表清单、镇街级清单、
# 以及新增的《全国无缝服装企业调研清单》都没有（它是工商底表型清单，
# 137 行里一行都没有产品列 —— 要它有产品，只能把备注里那句「品类待确认」
# 编成一个产品名）。全量要求会让这条断言变成「逼后来的人编字段」，
    # 所以只覆盖确实带产品列的那几份。
    with_products = [
        i for i in listed["items"]
        if "全国代表" not in " ".join(i["list_names"])
        and "镇街级" not in " ".join(i["list_names"])
        and "无缝服装" not in " ".join(i["list_names"])
        and "现代纺织与服装" not in " ".join(i["list_names"])
        # 医疗器械那份的品类在「产业」列（医疗器械·体外诊断），
        # 与「现代纺织·化纤」同类 —— 是产业标签，不是产品名。
        and "医疗器械" not in " ".join(i["list_names"])
    ]
    check(
        "带产品列的调研清单企业全部有主营产品",
        all(i["products"] for i in with_products),
        [i["name"] for i in with_products if not i["products"]][:3],
    )
    # 无缝服装那份有 7 行「其他产区」（揭阳普宁、湖州南浔、德清、东阳、海宁、诸暨、昆山…），
    # 那是作者自己写的「别处」，刻意不挂无缝产区 —— 挂错产区比不挂更难发现。
    # 其中 5 家落在浙江某个有主导集群的镇街，会被全站既有的**兜底规则**
    # 归入那个镇街的主导集群（南浔木地板、德清生物医药…），页面标「归属待确认」；
    # 剩下 2 家在浙江之外（普宁、昆山），连兜底都没有。
    # 这两条都要钉住：兜底是设计好的，但「没有任何归属」的家数不能悄悄涨。
    #
    # 必须翻完全部 828 家再判 —— `listed` 只是第 1 页（100 行），
    # 在这上面数「有几家没有归属」永远只会得到 0。
    everything: list[dict] = []
    for page_no in range(1, listed["meta"]["total"] // 100 + 2):
        _, chunk = call("GET", f"/api/enterprises?data_grade=site_curated&page_size=100&page={page_no}")
        everything.extend(chunk["items"])
    loose = [i for i in everything if not i["cluster_names"]]
    fallback = [i for i in everything if i.get("attribution_note")]
    check(
        # 60 家 = 415X 的 29 家（赛道只对应外地新星产业群）+ 无缝首版的 2 家（普宁、
        # 昆山）+ 气动的 29 家（3 条省份不是浙江的气动产业带：江苏（其他）8 / 广东（其他）3 /
        # 山东 10，外加「国产气动龙头/外资品牌在华」两条企业池里的省外 8 家）——
        # 气动那 29 家**刻意不挂**：那两条企业池是跨产区的，挂到某一条产业带上
        # 会让同一家企业在两个产区里各算一次（详见 README 气动元件那一节）。
        "整体口径：1959 家调研企业里 60 家无任何归属（省外 + 赛道只对应外地产业群 + 跨产区企业池）",
        len(everything) == 1959 and len(loose) == 60 and len(fallback) == 49,
        (len(everything), len(loose), len(fallback)),
    )
    # 415X 调研清单带来一批「赛道只对应外地新星产业群」的企业，它们本身就无集群可挂
    # （71 家）。所以上面那条「其他产区」的断言必须**按名单**圈定，否则会被这批数字污染。
    seamless_rows = [i for i in everything if any("无缝服装" in n for n in i["list_names"])]
    seamless_loose = [i for i in seamless_rows if not i["cluster_names"]]
    seamless_fallback = [i for i in seamless_rows if i.get("attribution_note")]
    check(
        "「其他产区」7 家不挂无缝产区：5 家走镇街兜底（标「归属待确认」）、2 家省外无归属",
        len(seamless_loose) == 2
        and {i["name"] for i in seamless_loose} == {"广东瑞源科技股份有限公司", "腾飞科技股份有限公司"}
        and len(seamless_fallback) == 5
        and all("归属待确认" in i["attribution_note"] for i in seamless_fallback),
        (len(seamless_rows), [i["name"] for i in seamless_loose][:4], len(seamless_fallback)),
    )

    # 详情页：荣誉的核实程度不能再写死「官方认定」
    sample = next(i for i in listed["items"] if i["verify_tier"].startswith("A"))
    status, detail = call("GET", f"/api/enterprises/{sample['id']}")
    check(
        "调研清单企业的荣誉不是「官方认定」",
        detail["honors"] and all(h["grade"] == "site_curated" for h in detail["honors"]),
        detail["honors"],
    )
    check("荣誉带核实档位", bool(detail["honors"][0].get("verify_tier")), detail["honors"][0])
    check(
        "授予单位不写成工信部",
        all("工业和信息化部" not in h["granted_by"] for h in detail["honors"]),
        detail["honors"][0]["granted_by"],
    )
    check(
        "联系方式来源逐字段降级标注",
        any("第三方调研" in v for v in detail["contact_sources"].values()),
        detail["contact_sources"],
    )

    # 只有调研来源的企业 = 我们自己一眼都没核过，页面上绝不能出现「核验日期」
    research_only = [
        i for i in listed["items"]
        if i["website"] and all("第三方调研" in v for v in i["contact_sources"].values())
    ]
    check("存在仅有调研来源的企业（用于校验来源标注）", bool(research_only), len(research_only))
    if research_only:
        status, only = call("GET", f"/api/enterprises/{research_only[0]['id']}")
        check(
            "没有本站核验过的记录不得带核验日期",
            not only.get("verified_at"),
            only.get("verified_at"),
        )
        check(
            "联系方式来源链接指向调研方引用页，而非伪造的核验页",
            only.get("source_url", "").startswith("http"),
            only.get("source_url"),
        )

    # 武义户外 / 义乌无缝本身就是官方第四批「新星」产业群（newstar-19 / newstar-18），
    # 所以企业直接挂在官方集群上，不再另建一个同名集群。
    status, cluster = call("GET", "/api/clusters/newstar-19-户外运动与休闲")
    check(
        "武义户外挂在官方新星产业群上（未另建重复集群）",
        cluster["slug"] == "newstar-19-户外运动与休闲" and cluster["enterprise_count"] == 65,
        cluster.get("enterprise_count"),
    )
    check("官方新星集群的核实程度仍是官方认定", cluster["data_grade"] == "official", cluster["data_grade"])
    check(
        "调研成果以别名叠加在官方集群上",
        "武义户外运动与休闲用品" in (cluster.get("aliases") or []),
        cluster.get("aliases"),
    )
    status, duplicate = call("GET", "/api/clusters/wuyi-outdoor")
    check("重复集群已清除（旧 slug 返回 404）", status == 404, status)
    check(
        "官方集群的备注说明调研补充与官方认定的区别",
        "官方集群名单" in (cluster.get("summary") or "") or "调研" in (cluster.get("summary") or ""),
        cluster.get("summary", "")[:60],
    )

    # CSV 里的哨兵值绝不能变成页面上的「官网」
    sentinels = ("未获取", "无官网", "占位符", "0000-00000000")
    sentinel_hits = []
    for page_no in range(1, 8):
        _, page = call("GET", f"/api/enterprises?page_size=100&page={page_no}")
        for item in page["items"]:
            for field in ("website", "public_phone", "public_email"):
                value = item.get(field) or ""
                if any(s in value for s in sentinels):
                    sentinel_hits.append((item["name"], field, value))
    check("全库没有哨兵值冒充联系方式", not sentinel_hits, sentinel_hits[:3])

    check(
        "新字段已开放（成立年份 / 注册资本 / 核实档位）",
        all(key in listed["items"][0] for key in ("founded_year", "registered_capital", "verify_tier")),
        sorted(listed["items"][0]),
    )

    section("统计口径")
    _, stats = call("GET", "/api/stats")
    check(
        "可见数 = 官方名单 + 第三方调研 + 政府公开（三档，不合并成一个口径）",
        stats["enterprise_total"] == 616 + 1959 + 584,
        stats["enterprise_total"],
    )
    check(
        # 457 → 454：改判了被「行政列写的是产区位置」带跑的企业 ——
        #   3 家舟山水产（中国水产舟山海洋渔业 / 舟山海洲水产 / 舟山震洋）的地市/
        #   县市区列写的是对标的「上海市普陀区」，而地址里的「普陀区沈家门街道」
        #   其实是舟山的（浙江与外省重名那一坑），按企业自己的地址改判回浙江。
        #   4 家境外・外省对标龙头（JUKI / 新秀丽 / Herman Miller / 鄂尔多斯）
        #   的地址列写的是产地标记而不是地址，它们照样记省外。
        "省外（对比产区 + 全国代表 + 镇街级对标龙头 + 医疗器械 + 415X + 产业带）单独计数，不混进浙江企业数",
        stats["enterprise_out_of_province"] == 685
        and stats["enterprise_total"] - stats["enterprise_out_of_province"] == 2474,
        stats["enterprise_out_of_province"],
    )

    status, seam = call("GET", "/api/clusters/newstar-18-无缝服装")
    check("义乌无缝针织服装集群建档", status == 200 and seam["enterprise_count"] == 95, seam.get("enterprise_count"))
    check("集群规模指标带口径与年份", len(seam["metrics"]) == 17 and all(m.get("caliber") and m.get("year") for m in seam["metrics"]), len(seam["metrics"]))
    check(
        "两组产能口径分开标注、不混用",
        {"设备能力口径", "产品产量口径"} <= {m["caliber"].split("（")[-1].rstrip("）") for m in seam["metrics"]},
        [m["caliber"] for m in seam["metrics"]],
    )
    check(
        "集群荣誉区分行业协会与政府认定",
        {h["grade"] for h in seam["honors"]} == {"media", "official"},
        [h["grade"] for h in seam["honors"]],
    )

    # 415X「现代纺织与服装」赛道：317 行的《现代纺织与服装企业调研清单》整份挂在它下面。
    # 这会把它从 31 家撑到 269 家 —— 混进 171 家省外企业。所以两条都要钉住：
    # ① 赛道本身的**官方**认定没被调研数据顶掉；② 省外那部分能被单独筛出来，
    # 不会让「浙江的现代纺织与服装集群有 269 家」成为一句假话。
    status, textile = call("GET", "/api/clusters/415X-%E7%8E%B0%E4%BB%A3%E7%BA%BA%E7%BB%87%E4%B8%8E%E6%9C%8D%E8%A3%85")
    check(
        "赛道集群仍以官方认定为准（调研清单只加企业，不改档位）",
        status == 200 and textile["data_grade"] == "official"
        and textile["enterprise_count"] == 269,
        (textile.get("data_grade"), textile.get("enterprise_count")),
    )
    _, in_cluster = call(
        "GET",
        "/api/enterprises?cluster=415X-%E7%8E%B0%E4%BB%A3%E7%BA%BA%E7%BB%87%E4%B8%8E%E6%9C%8D%E8%A3%85&scope=zhejiang&page_size=1",
    )
    _, out_cluster = call(
        "GET",
        "/api/enterprises?cluster=415X-%E7%8E%B0%E4%BB%A3%E7%BA%BA%E7%BB%87%E4%B8%8E%E6%9C%8D%E8%A3%85&scope=out_of_province&page_size=1",
    )
    check(
        "赛道集群里的企业能按产地拆开（浙江 98 / 省外 171），省外不混进浙江统计",
        in_cluster["meta"]["total"] + out_cluster["meta"]["total"] == textile["enterprise_count"]
        and out_cluster["meta"]["total"] == 171,
        (in_cluster["meta"]["total"], out_cluster["meta"]["total"]),
    )

    status, foreign = call("GET", "/api/clusters/shantou-gurao-seamless")
    check("省外产区单独建档", status == 200 and foreign["enterprise_count"] == 21, foreign.get("enterprise_count"))
    check("省外产区不挂浙江行政区", foreign["regions"] == [], foreign["regions"])
    # 修订版把「产区」细化到「地方 + 品类」，多出的两个产区各自建档：
    # 潮南（省外对比）与浦江（省内）不能混进谷饶那条，也不能都不挂。
    status, chaonan = call("GET", "/api/clusters/shantou-chaonan-underwear")
    check("汕头潮南单独建档（与谷饶分列，不混表）", status == 200 and chaonan["enterprise_count"] == 7, chaonan.get("enterprise_count"))
    check("潮南是省外对比产区，同样不挂浙江行政区", chaonan["regions"] == [], chaonan["regions"])
    status, pujiang = call("GET", "/api/clusters/pujiang-zhenzhi-fuzhuang")
    # 6 → 7：浙江省产业带调研清单里另有 1 家浦江针织企业，两条名单各给一次归属。
    check("浦江针织服装单独建档（省内调研产区）", status == 200 and pujiang["enterprise_count"] == 7, pujiang.get("enterprise_count"))
    check(
        "浦江是省内产区，要挂到浦江县（不是省外对比产区）",
        pujiang["district"] == "浦江县" and pujiang["regions"],
        (pujiang["district"], pujiang["regions"]),
    )

    # 省外企业在列表里必须能被筛出来、也能量excluding，不能混进浙江产区统计
    _, foreign_list = call("GET", "/api/enterprises?scope=out_of_province&page_size=20")
    check("省外企业可单独筛出", foreign_list["meta"]["total"] == 685, foreign_list["meta"]["total"])
    check(
        "省外企业都带省外标记",
        all(i["region_source"] == "out_of_province" for i in foreign_list["items"]),
        {i["region_source"] for i in foreign_list["items"]},
    )
    _, zh = call("GET", "/api/enterprises?scope=zhejiang&page_size=1")
    check(
        "「只看浙江」会排除省外对比产区",
        zh["meta"]["total"] == stats["enterprise_total"] - foreign_list["meta"]["total"],
        (zh["meta"]["total"], stats["enterprise_total"]),
    )

    status, sources = call("GET", "/api/stats/sources")
    check("来源声明含第四批集群名单", any("第四批" in s["name"] for s in sources["sources"]), "")
    check("来源声明含人工补充档", any("人工补充" in s["name"] for s in sources["sources"]), "")
    check(
        "来源声明含搜索摘要档（未核验原站）",
        any("搜索结果摘要" in s["name"] and "未核验" in s["used_for"] for s in sources["sources"]),
        "",
    )
    check(
        "来源声明含武义户外运动与休闲调研清单",
        any("武义" in s["name"] and "第三方调研" in s["name"] for s in sources["sources"]),
        "",
    )
    check(
        "来源声明含全国无缝服装调研清单",
        any("无缝服装" in s["name"] and "第三方调研" in s["name"] for s in sources["sources"]),
        "",
    )
    check(
        "来源声明含第四批集群全国代表企业清单",
        any("全国代表" in s["name"] for s in sources["sources"]),
        "",
    )
    check(
        "来源声明写明搜索摘要层已停止扩展",
        any("搜索结果摘要" in s["name"] and "停止扩展" in s["used_for"] for s in sources["sources"]),
        "",
    )
    reps_source = next((s for s in sources["sources"] if "全国代表" in s["name"]), {})
    check(
        "来源声明里的数字来自注册表，不是写死的（名单修订后不会留旧话）",
        "387 条 / 385 家" in reps_source.get("used_for", "")
        and reps_source.get("file", "").endswith("_副本.csv"),
        (reps_source.get("file"), reps_source.get("used_for", "")[:46]),
    )
    check(
        "来源声明的数字与注册表登记的一致",
        reps_source.get("imported_at") == (
            json.loads((ROOT / "data" / "curated" / "imported_lists.json").read_text(encoding="utf-8"))
            ["lists"][0]["imported_at"]
        ),
        reps_source.get("imported_at"),
    )
    check(
        "来源声明的边界写明 C 档建议未被采纳",
        any("未采纳" in b and "C 档" in b for b in sources["boundaries"]),
        "",
    )
    sources_grades = {g["key"] for g in sources["grades"]}
    check(
        "来源声明的名单核实程度不再一律写成官方认定",
        {s["grade"] for s in sources["sources"]} >= {"official", "site_curated"},
        {s["grade"] for s in sources["sources"]},
    )
    check(
        "边界声明要求官方名单与第三方清单分别计数",
        any("不得合并" in b for b in sources["boundaries"]),
        "",
    )
    check("核实程度为五档", len(sources["grades"]) == 5, len(sources["grades"]))
    # 首页把 list_breakdown 整表渲染成卡片。表里既有官方名单也有第三方调研清单，
    # 所以每张卡的档位必须**跟着数据走**，不能在前端写死一句话。
    _, stats_now = call("GET", "/api/stats")
    check(
        "名单登记的每条都带自己的核实程度与标签（首页不再写死「官方认定」）",
        all(
            entry.get("grade") in sources_grades and entry.get("grade_label")
            for entry in stats_now["list_breakdown"]
        )
        and {e["grade"] for e in stats_now["list_breakdown"]} >= {"official", "site_curated"},
        [(e["name"][:18], e["grade"], e.get("grade_label")) for e in stats_now["list_breakdown"]],
    )
    check(
        "官方名单数与其它来源数分别给出（KPI 里那句话要能对上）",
        stats_now["enterprise_official"] == 616
        and stats_now["enterprise_total"] - stats_now["enterprise_official"] == 1959 + 584,
        (stats_now["enterprise_official"], stats_now["enterprise_total"]),
    )
    check(
        "边界声明含退出条款",
        any("不撮合" in b or "不收款" in b for b in sources["boundaries"]),
        "",
    )
    # 对外材料不出现「抓取 / 爬虫 / 爬取」：标签要回答「这个值是谁给的、核到什么程度」，
    # 而「抓取」描述的是我们怎么做 —— 读者会把它误读成「这家企业已被本站查过」。
    # 这条断言覆盖**接口返回的整份来源声明 + 全库联系方式来源标签**，
    # 因为这两处都是对外展示的，且都由后端的 LAYER_LABELS / used_for 生成。
    forbidden = ("抓取", "爬虫", "爬取")
    source_text = json.dumps(sources, ensure_ascii=False)
    check(
        "来源声明里不出现「抓取 / 爬虫 / 爬取」",
        not any(word in source_text for word in forbidden),
        [word for word in forbidden if word in source_text],
    )
    label_text: set[str] = set()
    for page_no in range(1, 5):
        _, chunk = call("GET", f"/api/enterprises?page_size=100&page={page_no}")
        for item in chunk["items"]:
            label_text.update((item.get("contact_sources") or {}).values())
    check(
        "联系方式来源标签里不出现「抓取 / 爬虫 / 爬取」",
        not any(word in label for label in label_text for word in forbidden),
        [label for label in label_text if any(w in label for w in forbidden)],
    )
    check(
        "联系方式来源标签已换成核验口径的说法",
        any("站方核验 · 已比对官网" in label for label in label_text)
        and not any("自动发现" in label for label in label_text),
        sorted(label_text),
    )
    # 来源地图这张表是给读者看的，不是给维护者看的：
    #   * 页面上是纯文本插值，写 `**加粗**`、`` `官网类型` `` 会原样显示出来；
    #   * 库表字段名（role 字段 / source_url / verified_at）读者看不懂，
    #     要说成「哪些县属于哪个集群」「附官网来源页与核验日期」。
    # 这两类都不想等到有人打开页面才发现，所以在接口层拦。
    leaks = [
        token
        for token in ("**", "`", "role 字段", "source_url", "verified_at")
        if token in source_text
    ]
    check("来源声明不残留 Markdown 记号与库表字段名", not leaks, leaks)
    roleless = [s["name"][:24] for s in sources["sources"] if not (s.get("role") or "").strip()]
    check(
        "每条来源都说明它在库里的角色（否则来源地图那一行只剩用途长句）",
        not roleless,
        roleless,
    )

    section("浙江省镇街级块状经济企业清单（第五份）")
    # 按产区找企业要走 `cluster=slug`（前端也是这么调的）：`q` 只查名字/简介/地区/产品，
    # 不查集群归属 —— 镇街级企业的名字里通常没有产区名，靠 q 搜是搜不到的。
    _, town = call("GET", "/api/enterprises?cluster=fenshui-zhibi&page_size=100")
    check(
        "按产区 slug 能筛出该产区的企业（企业名里不含产区名）",
        town["meta"]["total"] == 7 and all("分水制笔" in i["cluster_names"] for i in town["items"]),
        [(i["name"], i["cluster_names"]) for i in town["items"][:2]],
    )
    status, fenshui = call("GET", "/api/clusters/fenshui-zhibi")
    check("已有产区（分水制笔）扩到 7 家", status == 200 and fenshui["enterprise_count"] == 7, fenshui.get("enterprise_count"))
    status, jintang = call("GET", "/api/clusters/dinghai-jintang")
    check(
        "新建镇街产区建档并挂上企业（金塘螺杆 5 家）",
        status == 200 and jintang["enterprise_count"] == 5 and jintang["data_grade"] == "site_curated",
        (status, jintang.get("enterprise_count")),
    )
    check(
        "镇街产区的规模档是「块状经济」，页面上单独标档",
        jintang["scale_tier"] == "块状经济",
        jintang["scale_tier"],
    )
    # 同一个产业带的两种叫法必须落到**同一个**集群，不能各建一条
    status, ruian = call("GET", "/api/clusters/ruian-qimopei")
    check(
        "「塘下汽摩配」并进「瑞安汽摩配」，没有新建成第二条档案",
        status == 200 and ruian["name"] == "瑞安汽摩配",
        (status, ruian.get("name")),
    )
    _, tangxia = call("GET", "/api/clusters?q=" + quote("塘下汽摩配"))
    check("搜不到第二条「塘下汽摩配」集群档案", tangxia["meta"]["total"] == 0, tangxia["meta"]["total"])
    # 对标龙头：标省外，且其地址不能把产区位置带跑
    _, bench = call("GET", "/api/enterprises?q=" + quote("施耐德"))
    check(
        "对标龙头标为省外（不参与浙江统计）",
        bench["meta"]["total"] >= 1 and bench["items"][0]["data_grade"] == "site_curated",
        [(i["name"], i["city"]) for i in bench["items"]],
    )
    _, zhouxiang = call("GET", "/api/clusters/yueqing-hongqiao")
    check("虹桥电子元器件的产区位置不被对标龙头带走", zhouxiang["city"] == "温州市", zhouxiang["city"])
    # 手机号与「藏在邮箱里的手机号」
    _, all_ent = call("GET", "/api/enterprises?page_size=100")
    check(
        "接口未返回任何把手机号写进邮箱本地部分的地址",
        not any(
            re.sub(r"[\s._-]", "", (i.get("public_email") or "x@y.z").split("@")[0]).isdigit()
            and len(re.sub(r"[\s._-]", "", i["public_email"].split("@")[0])) == 11
            for i in all_ent["items"]
            if i.get("public_email")
        ),
        [(i["name"], i["public_email"]) for i in all_ent["items"] if i.get("public_email")][:3],
    )

    section("医疗器械企业调研清单（第四份调研清单）")
    # 这份清单的性质与前几份不同：它的产区是「市级归口」的（成员散在好几个县区），
    # 于是有了本库第一个「只挂到市一级」的集群角色（市级归口）。
    _, bio = call("GET", "/api/clusters/415X-生物医药与医疗器械")
    # 接口没有「按名单筛企业」这个参数，所以「这份清单进了多少家」只能这样量：
    # 赛道的成员企业里，list_names 带这份清单的家数。
    # 它比 194 多，是因为 115 家产区行里还有一部分按产品/地区另外落到了这条赛道上，
    # 而一家企业可以同时属于产区与赛道。
    bio_members = [e for e in bio["enterprises"] if any("医疗器械" in (n or "") for n in (e["list_names"] or []))]
    check(
        "整份清单并入 415X-生物医药与医疗器械 赛道（赛道 238 家，含这份清单 200+ 家）",
        bio["data_grade"] == "official"
        and bio["enterprise_count"] == 238
        and len(bio_members) >= 194,
        (bio["data_grade"], bio["enterprise_count"], len(bio_members)),
    )
    _, cured = call("GET", "/api/clusters?q=省内调研产区&page_size=100")
    new_areas = {c["slug"]: c for c in cured["items"] if c["slug"] in MEDICAL_AREA_SLUGS}
    check(
        "9 个医疗器械产区都建了档（站方整理档，不是官方认定）",
        len(new_areas) == 9
        and all(c["data_grade"] == "site_curated" and c["data_grade_label"] == "站方整理" for c in new_areas.values()),
        sorted(new_areas),
    )
    check(
        "115 家按清单自带的「块状经济」列落位到 9 个产区（家数与清单一致）",
        {slug: c["enterprise_count"] for slug, c in new_areas.items()} == MEDICAL_AREA_COUNTS,
        {slug: c["enterprise_count"] for slug, c in new_areas.items()},
    )
    check(
        "市级归口的产区只挂到市一级，不编一个县区出来（桐庐/东阳这两条是县级）",
        new_areas["hangzhou-gaoduan-yiliaoqixie"]["city"] == "杭州市"
        and new_areas["hangzhou-gaoduan-yiliaoqixie"]["district"] == ""
        and new_areas["tonglu-neikuijing"]["district"] == "桐庐县"
        and new_areas["dongyang-yiliaoqixie"]["district"] == "东阳市",
        [
            (s, c["city"], c["district"])
            for s, c in sorted(new_areas.items())
        ],
    )
    # 调研方自己写明：候选池里剔掉了 28 家「名字带医疗但主业不是」的
    # （眼药、中药、按摩器具、眼镜镜片）。这份清单收不收，决定了库里会不会
    # 冒出「医疗器械企业」里坐着一家中药厂。抽 3 家剔掉的确认没进来；
    # 明月镜片是个好例子 —— 它本来就在库里（镇街级清单拿它对标眼镜产区），
    # 所以只能断言它**不是靠这份清单**进来的。
    check(
        "只收器械不收药品：被调研方剔除的眼药/中药/按摩器具不在库里",
        all(call("GET", "/api/enterprises?q=" + name)[1]["meta"]["total"] == 0
            for name in ("兴齐眼药", "倍轻松", "桐君堂")),
    )
    _, glasses = call("GET", "/api/enterprises?q=明月镜片")
    check(
        "明月镜片只以「对标龙头」身份在库，没被医疗器械清单收进去",
        glasses["meta"]["total"] == 1
        and not any("医疗器械" in n for n in glasses["items"][0]["list_names"]),
        glasses["items"][0]["list_names"],
    )

    section("登录系统的持久性：灌库/名单修订不该把人踢下线")
    # 「账号还在不在」由 test_tools.py 结构性断言（灌库的删除清单里没有 app_user）
    # 守着；这里守的是接口契约：收藏按**企业名**关联，所以数据层的 id 变了也照样对上。
    _, fav_meta = call("GET", "/api/account/me", token=token)
    check(
        "账号信息里分别给出可展示的收藏数与失效收藏数",
        "favorite_count" in fav_meta and "stale_count" in fav_meta,
        fav_meta,
    )
    _, fav_list = call("GET", "/api/account/favorites", token=token)
    check(
        "收藏列表把「企业已不在名单里」的条目单独列出来，而不是静默丢掉",
        "stale" in fav_list and isinstance(fav_list["stale"], list),
        list(fav_list),
    )
    check(
        "收藏总数与列表长度一致（不能出现「说有 5 家只列 3 家」）",
        fav_list["total"] == len(fav_list["items"]) == fav_meta["favorite_count"],
        (fav_list["total"], len(fav_list["items"]), fav_meta["favorite_count"]),
    )
    status, _ = call("DELETE", "/api/account/favorites/unresolved")
    check("清理失效收藏需要登录", status == 401, status)

    # 登录失败限流：拿一个一次性用户名试穿，别影响别的用例
    throttle_user = f"throttle_{uuid.uuid4().hex[:8]}"
    codes = [
        call("POST", "/api/account/login", {"username": throttle_user, "password": "wrongpass1"})[0]
        for _ in range(12)
    ]
    check(
        "连续猜密码会被挡住（前面是 401，之后出现 429）",
        codes[0] == 401 and 429 in codes,
        codes,
    )

    section("产业带档案编辑接口（默认关闭）")
    status, _ = call("GET", "/api/admin/clusters")
    check("未带令牌时产业带编辑接口不可用", status in (403, 404), status)
    status, _ = call("PATCH", "/api/admin/clusters/newstar-18-%E6%97%A0%E7%BC%9D%E6%9C%8D%E8%A3%85", {"summary": "x"})
    check("未带令牌时不能编辑产业带", status in (403, 404), status)

    if not admin_token:
        print("  SKIP  未设置 ADMIN_TOKEN，跳过产业带编辑往返")
    else:
        slug_path = "/api/admin/clusters/newstar-18-%E6%97%A0%E7%BC%9D%E6%9C%8D%E8%A3%85"
        status, listed = call("GET", "/api/admin/clusters", token=admin_token)
        check(
            "产业带列表可查（166 = 139 + 气动元件 27 条派生产区）",
            status == 200 and listed["total"] == 166,
            listed["total"],
        )
        check(
            "列表给出当前生效的规模指标与荣誉数量（编辑前先看得见现状）",
            all({"metric_count", "honor_count", "has_edit"} <= set(row) for row in listed["items"]),
            listed["items"][0] if listed.get("items") else None,
        )
        status, detail = call("GET", slug_path, token=admin_token)
        check(
            "详情返回**当前生效**的值供表单预填",
            status == 200 and detail["cluster"]["slug"] == "newstar-18-无缝服装" and "metrics" in detail["cluster"],
            status,
        )
        status, bad = call(
            "PATCH",
            slug_path,
            {"metrics": [{"value": 1, "unit": "家"}]},
            token=admin_token,
        )
        check("裸数字（缺口径/年份/来源）被 422 挡住", status == 422, (status, str(bad)[:80]))
        status, bad = call("PATCH", slug_path, {"name": "改名试试"}, token=admin_token)
        check("集群名称不可通过本接口编辑", status == 422, (status, str(bad)[:80]))

        # 一次完整往返：写入 → 撤销。撤销放在断言之前，保证无论成败都不留残留。
        status, saved = call(
            "PATCH",
            slug_path,
            {"summary": "回环测试简介", "summary_grade": "site_curated", "aliases": ["回环测试别名"]},
            token=admin_token,
        )
        status_revert, reverted = call("DELETE", slug_path, token=admin_token)
        check(
            "编辑可写入、可撤销（撤销后叠加文件里没有这条）",
            status == 200
            and status_revert == 200
            and reverted["reverted"]
            and all(
                entry.get("slug") != "newstar-18-无缝服装"
                for entry in json.loads(
                    (ROOT / "data" / "curated" / "cluster_edits.json").read_text(encoding="utf-8")
                )["edits"]
            ),
            (status, status_revert, saved.get("saved") if isinstance(saved, dict) else saved),
        )
        check(
            "保存后必须重新灌库才生效（接口自己把这句话说清楚）",
            isinstance(saved, dict) and "seed --drop" in saved.get("note", ""),
            (saved or {}).get("note") if isinstance(saved, dict) else saved,
        )
        status, _ = call("DELETE", slug_path, token=admin_token)
        check("没有编辑记录时撤销返回 404", status == 404, status)

    section("人工补充：保存通路与拒绝理由（这一节要有 ADMIN_TOKEN 才有意义）")
    if not admin_token:
        print("  SKIP  未设置 ADMIN_TOKEN，跳过人工补充往返")
    else:
        # ① 保存：走的是网页表单同一条接口
        # 用接口自己的筛选（不要在一页数据里碰运气），确保一定找得到目标。
        # 而且**必须挑一条当前没有人工补充记录的**企业：这个用例会写入再删除，
        # 挑中用户自己补过的条目就会把别人的数据删掉（我这么干过一次）。
        _, manual_before = call("GET", "/api/admin/contacts", token=admin_token)
        already = {r["name"] for r in manual_before["companies"]}
        _, empty_page = call("GET", "/api/enterprises?contact_status=empty&page_size=20")
        check("待补充清单非空（否则这一节测不出东西）", empty_page["meta"]["total"] > 0, empty_page["meta"]["total"])
        target = next(i for i in empty_page["items"] if i["name"] not in already)
        payload = {
            "name": target["name"],
            "public_phone": "0571-63750000",
            "address": "杭州市临安区玲珑街道测试路1号",
            "source_url": "https://example.com/contact",
            "note": "回合验证（测试写入，随后清理）",
        }
        status, saved = call("POST", "/api/admin/contacts", payload, token=admin_token)
        check(
            "人工补充可以保存（写入 manual_sites.json 并标记为「人工补充」）",
            status in (200, 201) and saved.get("record", {}).get("name") == target["name"],
            (status, str(saved)[:80]),
        )
        status, manual = call("GET", "/api/admin/contacts", token=admin_token)
        entry = next((r for r in manual["companies"] if r["name"] == target["name"]), None)
        check(
            "保存后立刻能在管理接口里读到（不是「提示成功了但没写进去」）",
            status == 200 and entry is not None and entry["public_phone"] == "0571-63750000",
            entry,
        )

        # ② 拒绝：手机号必须被挡住，理由是具体的一句话
        status, rejected = call(
            "POST",
            "/api/admin/contacts",
            {"name": target["name"], "public_phone": "13812345678"},
            token=admin_token,
        )
        check(
            "个人手机号被拒绝，并给出可读的理由（不是只说 422）",
            status == 422 and "手机号" in str(rejected),
            (status, str(rejected)[:80]),
        )

        # ③ 拒绝：名单外的企业名
        status, rejected = call(
            "POST",
            "/api/admin/contacts",
            {"name": "这家公司不存在有限公司", "public_phone": "0571-63750001"},
            token=admin_token,
        )
        check("名单外的企业被拒绝（避免产生无依据记录）", status == 422, status)

        # ④ 撤回：确认删掉的是**这次**写入的那条，且撤回后接口读不到了
        # 企业名走查询参数（真实数据里有含斜杠的名字，路径参数会落到 404）
        status, cleaned = call("DELETE", f"/api/admin/contacts?name={quote(target['name'])}", token=admin_token)
        _, manual_after = call("GET", "/api/admin/contacts", token=admin_token)
        check(
            "测试写入可以撤回（管理接口提供回滚，且撤回后读不到）",
            status == 200
            and cleaned.get("removed") is True
            and all(r["name"] != target["name"] for r in manual_after["companies"]),
            (status, cleaned),
        )
        status, again = call("DELETE", f"/api/admin/contacts?name={quote(target['name'])}", token=admin_token)
        check("重复撤回返回 404（而不是静默成功）", status == 404, (status, str(again)[:60]))

    section("管理令牌的配置来源")
    # 管理令牌可以来自环境变量，也可以写在 backend/.env 里。第二种最自然，
    # 但 pydantic-settings 只把**声明过的字段**读进 Settings，不会写回 os.environ ——
    # 曾经因此让「把令牌写进 .env」完全不生效（接口永远 404，看起来像功能没启用）。
    env_token = (os.environ.get("ADMIN_TOKEN") or "").strip()
    env_file = ROOT / "backend" / ".env"
    file_token = ""
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            if line.startswith("ADMIN_TOKEN="):
                file_token = line.split("=", 1)[1].strip()
                break
    if file_token and not env_token:
        status, _ = call("GET", "/api/admin/lists", token=file_token)
        check(
            "backend/.env 里配的 ADMIN_TOKEN 能启用管理接口（不只是环境变量）",
            status == 200,
            status,
        )
    else:
        print("  SKIP  backend/.env 未配 ADMIN_TOKEN 或已由环境变量覆盖")

    section("名单导入接口（默认关闭）")
    # 管理接口的令牌是运维侧的环境变量，测试里不假装知道它：
    # 只断言「没有令牌时不会成功」，有令牌时（ADMIN_TOKEN 已导出）才走完整往返。
    status, _ = call("GET", "/api/admin/lists")
    check("未带令牌时名单导入接口不可用", status in (403, 404), status)
    status, _ = call("POST", "/api/admin/lists/import", {"list_name": "x"})
    check("未带令牌时不能导入", status in (403, 404), status)

    if not admin_token:
        print("  SKIP  未设置 ADMIN_TOKEN，跳过导入往返（另跑 tests/test_tools.py 覆盖解析规则）")
    else:
        status, listed = call("GET", "/api/admin/lists", token=admin_token)
        check("已导入名单可查（注册表）", status == 200 and listed["lists"], status)
        check(
            "注册表里的名单指向真实存在的产物文件",
            all(
                (ROOT / "data" / "curated" / entry["layer_json"]).exists()
                and (ROOT / "data" / "source" / entry["source_json"]).exists()
                for entry in listed["lists"]
            ),
            [entry["source_key"] for entry in listed["lists"]],
        )
        csv_path = ROOT / "docs" / "浙江第四批产业集群_全国代表企业清单_副本.csv"
        status, dry = call_multipart(
            "/api/admin/lists/import",
            csv_path,
            {
                "list_name": "干跑（第三方调研）",
                "source_key": "dry_run_api",
                "grade": "site_curated",
                "source_file": csv_path.name,
                "dry_run": "true",
            },
            token=admin_token,
        )
        check("干跑返回整份名单的报告", status == 200 and dry["rows"] == 387, status)
        check(
            "干跑报告说清这批企业与库里已有企业的关系、以及有没有挂不上的集群",
            # 这次干跑用的是**新的** source_key（下面还要据此断言「干跑不写注册表」），
            # 所以「本次要覆盖的名单」是空的：这份 CSV 已经在注册表里登记为
            # cluster_reps，它的 385 家在报告里确实属于「别的名单」。
            # 摘除规则本身在**覆盖同一份名单**的情形下才有意义，见下一条断言。
            dry["database"]["new_to_db"] + dry["database"]["also_in_other_lists"]
            <= dry["database"]["incoming"]
            and dry["cluster_mapping"]["unresolved"] == []
            and len(dry["cluster_mapping"]["requests"]) == 35,
            (dry["database"], len(dry["cluster_mapping"]["requests"])),
        )
        # 换个 source_key 就是「这次要覆盖哪一份名单」。换成它真正对应的 cluster_reps
        # 之后，「已在别的名单里」不能把这份名单自己算进去 —— 不摘的话修订版名单会
        # 100% 命中自己的旧行，那个数字就没有信息了。
        # 干跑**不写文件**（`import_list` 在 dry_run 处就返回），所以这里敢用真 key；
        # 顺手把注册表前后比对一下，等于把这句「不写」也钉住了。
        registry_path = ROOT / "data" / "curated" / "imported_lists.json"
        registry_before = registry_path.read_bytes()
        status, covering = call_multipart(
            "/api/admin/lists/import",
            csv_path,
            {
                "list_name": "干跑（第三方调研）",
                "source_key": "cluster_reps",
                "grade": "site_curated",
                "source_file": csv_path.name,
                "dry_run": "true",
            },
            token=admin_token,
        )
        registry_after = registry_path.read_bytes()
        if registry_after != registry_before:
            # 万一真写坏了，先还原再让断言去报错，别把库留给下一个人收拾
            registry_path.write_bytes(registry_before)
        check("干跑连已登记的条目也不改写", registry_after == registry_before)
        check(
            "干跑按 source_key 摘掉本次要覆盖的名单，再数「已在别的名单里」",
            status == 200
            and 0 < covering["database"]["also_in_other_lists"] < covering["database"]["incoming"],
            covering.get("database", covering),
        )
        _, again = call("GET", "/api/admin/lists", token=admin_token)
        check(
            "干跑没有写进注册表",
            all(entry["source_key"] != "dry_run_api" for entry in again["lists"]),
            [entry["source_key"] for entry in again["lists"]],
        )
        status, bad = call_multipart(
            "/api/admin/lists/import",
            csv_path,
            {"list_name": "坏表（第三方调研）", "source_key": "bad_api", "grade": "site_curated"},
            token=admin_token,
            override_bytes="序号,官网\n1,a.com\n".encode(),
        )
        check("缺企业名列的 CSV 被 422 挡住（不落盘）", status == 422, (status, str(bad)[:60]))

    section("重新灌库接口（默认关闭）")
    if admin_token:
        status, _ = call("POST", "/api/admin/lists/reseed", token="wrong-token")
        check("令牌不对时不能灌库（不是只藏在页面上）", status == 403, status)

        # 先注册一个账号并收藏一家企业：灌库若把用户数据删了，这两条会立刻断。
        # 用真实接口数，而不是直接查库 —— 探针本身也要走被测的那条路径。
        probe = f"reseed_{uuid.uuid4().hex[:8]}"
        call("POST", "/api/account/register", {"username": probe, "password": "reseed-pass-1"})
        _, login = call("POST", "/api/account/login", {"username": probe, "password": "reseed-pass-1"})
        probe_token = login.get("token") if isinstance(login, dict) else None
        _, listed_all = call("GET", "/api/enterprises?page_size=1&scope=all")
        target_id = listed_all["items"][0]["id"]
        call("POST", "/api/account/favorites", {"enterprise_id": target_id, "note": "灌库前"}, token=probe_token)

        _, stats_before = call("GET", "/api/stats")
        # 灌库会逐行 flush 2,699 家（约 30-45 秒，含删表重建），
        # 用默认 25 秒会假超时 —— 那是**测试判据**的问题，不是功能坏了。
        status, reseeded = call("POST", "/api/admin/lists/reseed", token=admin_token, timeout=240)
        check("可以按 data/ 清单重新灌库", status == 200 and reseeded.get("ok"), (status, str(reseeded)[:80]))
        check(
            "灌库返回各表计数，调用方能确认确实生效了",
            status == 200 and {"regions", "clusters", "enterprises"} <= set(reseeded.get("counts", {})),
            reseeded.get("counts"),
        )
        _, stats_after = call("GET", "/api/stats")
        check(
            "灌库后企业数与灌库前一致（清单没变，结果就不该变）",
            stats_after["enterprise_total"] == stats_before["enterprise_total"],
            (stats_before["enterprise_total"], stats_after["enterprise_total"]),
        )
        # 这两条是本接口存在的**唯一理由**：更新名单不该让所有人重新注册、收藏清零。
        status, me_after = call("GET", "/api/account/me", token=probe_token)
        check("灌库后原账号仍能登录（令牌还有效，说明账号没被删）", status == 200, (status, str(me_after)[:60]))
        status, favs_after = call("GET", "/api/account/favorites", token=probe_token)
        check(
            "灌库后收藏仍在（收藏按企业名关联，重灌不丢）",
            status == 200 and any(f.get("note") == "灌库前" for f in favs_after.get("items", [])),
            (status, str(favs_after)[:120]),
        )
        _, health_after = call("GET", "/api/health")
        check("灌库之后库仍然可用（不是把连接弄断了）", health_after.get("database") == "up", health_after)

    print(f"\n=== {passed} passed, {failed} failed ===")
    if failures:
        print("失败项：")
        for name in failures:
            print("  -", name)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
