"""Multi-strategy official-site discovery for the Zhejiang enterprise directory.

Search engines block scripted access (Bing RSS returns irrelevant hits, so.com
throws a captcha, Baidu is unstable), and ICP-备案 lookup endpoints are JS-gated
or 403. So discovery here is **deterministic and verification-gated** instead:

  1. **Candidate generation** — from a curated seed/brand map, plus pinyin
     variants of the company name (full pinyin, pinyin with generic words
     stripped, initials, brand-first-2/3-chars) across ``.com`` / ``.cn`` /
     ``.com.cn``.
  2. **DNS pre-filter** — only domains that actually resolve are worth an HTTP
     request. Cheap and threaded.
  3. **Strict content verification** — the fetched page must contain the
     company's full Chinese name, or its distinctive core name in the page
     ``<title>``. This is the quality gate: a resolved domain proves nothing
     (``zy.com``, ``smk.com`` are owned by unrelated parties), only the page
     content does.
  4. **Contact extraction** — reuses the hardened extractor from
     ``enrich_companies`` (switchboard/400 + general mailbox only; mobile
     numbers are never captured).

Every accepted record carries ``source_url`` (the page that proved the match),
``verified_at`` and ``discovery_method`` so the UI can show how it was obtained.
Company pages that fail to verify stay empty rather than guessed — those are the
ones filled by hand via ``data/curated/manual_sites.json``.

Usage::

    python tools/discover_sites.py --measure 40        # sample hit rate, no writes
    python tools/discover_sites.py --limit 100         # first 100 not yet verified
    python tools/discover_sites.py                     # everything outstanding
    python tools/discover_sites.py --only 天能 --refresh
"""

from __future__ import annotations

import argparse
import concurrent.futures as cf
import json
import re
import socket
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from enrich_companies import (  # noqa: E402
    http_get,
    is_blocked,
    extract_contacts,
    load_seed,
    now_iso,
    read_cache,
    write_cache,
)
from companies import load_companies  # noqa: E402

try:
    from pypinyin import lazy_pinyin
except ImportError:  # pragma: no cover
    lazy_pinyin = None

OUT = ROOT / "data" / "curated" / "discovered.json"

# 站点验收规则版本。改动验收逻辑时必须 +1。
#
# 注意：v3 时代「旧的否定结论可以安全复用」的推理已不成立 —— 那条推理的前提是
# 规则只会越来越严。v4 是**放宽**的（新增 body-selfid 路径），所以 v3 写下的
# 否定缓存里恰好含有我们现在想找回的真命中（huier.org、giantkone.com 都是），
# 必须一并失效重判。跨版本复用否定结论的逻辑已按此收紧。
RULE_VERSION = 4

# 版权/备案上下文标记。企业全称出现在这些标记附近时，可判定该页面就是企业自己的站点，
# 而不是「某页面提到过这家企业」。
COPYRIGHT_MARKERS = re.compile(r"版权|Copyright|©|ICP备|京公网安备|主办单位|网站备案|版权所有")
COPYRIGHT_WINDOW = 90

# 自我介绍句式。企业全称**紧接**这些词出现时，说明这句话的主语就是该企业，
# 例如「杭州惠耳听力技术设备有限公司是一家专业从事…」。地址行里的全称
# （「地址：…，杭州信多达智能科技有限公司 联系电话：…」）不满足该句式。
SELF_ID_FRAMES = re.compile(
    r"^(?:是|系|成立|创建|创立|拥有|坐落|位于|始创|始建|主要从事|专业从事|以下简称|专注|致力于)"
)
# 标题里出现的其他公司主体，用于识别「同集团/兄弟公司的站点」。
OTHER_COMPANY = re.compile(r"[\u4e00-\u9fa5]{2,18}(?:股份有限公司|有限公司)")
# 只剥标点/空白，不剥字：句式的判定必须基于紧邻的字符。
JOINERS = re.compile(r"[\s，,、。；;：:()（）\-—]+")

SCRIPT_STYLE = re.compile(r"(?is)<script.*?</script>|<style.*?</style>|<noscript.*?</noscript>")

# 哪些旧版本规则写下的**否定**结论在当前规则下依然有效。
# 判据：当前规则相对该版本只收紧、不放松。v4 放松了规则，故为空集。
NEGATIVE_REUSE_VERSIONS: frozenset[int] = frozenset()

# 单站抓取超时（秒）。中小企业站点普遍很慢，但超过这个秒数的站点也不是可靠线索；
# 全量跑批的耗时基本由它决定，所以做成可调参数。
VERIFY_TIMEOUT = 14
# 单企业候选的并发校验度。打的是候选企业自己的站，不是搜索引擎，不影响限流。
VERIFY_WORKERS = 6

CITY_PREFIXES = (
    "杭州市", "宁波市", "温州市", "嘉兴市", "湖州市", "绍兴市", "金华市",
    "衢州市", "舟山市", "台州市", "丽水市", "浙江省", "浙江", "杭州", "宁波",
    "温州", "嘉兴", "湖州", "绍兴", "金华", "衢州", "舟山", "台州", "丽水",
    "中国", "乐清", "瑞安", "永嘉", "平阳", "苍南", "龙港", "诸暨", "嵊州",
    "新昌", "海宁", "平湖", "桐乡", "嘉善", "海盐", "德清", "长兴", "安吉",
    "余姚", "慈溪", "宁海", "象山", "兰溪", "义乌", "东阳", "永康", "武义",
    "浦江", "磐安", "江山", "龙游", "常山", "开化", "温岭", "临海", "玉环",
    "天台", "仙居", "三门", "龙泉", "青田", "缙云", "遂昌", "松阳", "云和",
    "庆元", "景宁", "岱山", "嵊泗", "上虞", "柯桥", "余杭", "萧山", "富阳",
    "临安", "临平", "钱塘", "北仑", "镇海", "鄞州", "奉化", "海曙", "江北",
    "鹿城", "龙湾", "瓯海", "洞头", "南湖", "秀洲", "吴兴", "南浔", "越城",
    "婺城", "金东", "柯城", "衢江", "定海", "普陀", "椒江", "黄岩", "路桥",
    "莲都", "景宁畲族",
)

SUFFIXES = (
    "集团股份有限公司", "股份有限公司", "集团有限公司", "控股集团有限公司",
    "科技股份有限公司", "有限责任公司", "有限公司", "科技集团", "控股集团",
    "研究院有限公司", "研究院", "集团", "公司",
)

# 企业名里的通用词，生成候选域名时剔除（"科技""股份""有限"等不参与品牌名）
GENERIC_WORDS = (
    "科技", "技术", "股份", "有限", "集团", "实业", "控股", "发展", "电子",
    "材料", "智能", "信息", "机械", "设备", "制造", "工业", "自动化", "新能源",
    "精密", "高新", "工程", "系统", "网络", "电气", "仪器", "仪表", "化工",
)

TLDS = ("com", "cn", "com.cn", "net", ".cn")

# 知名企业的官方域名（站方整理；仅对官方名单内企业生效，由 prune 步骤校验）
BRAND_DOMAINS: dict[str, str] = {
    "浙江大华技术股份有限公司": "www.dahuatech.com",
    "杭州海康机器人股份有限公司": "www.hikrobotics.com",
    "杭州海康威视数字技术股份有限公司": "www.hikvision.com",
    "先临三维科技股份有限公司": "www.shining3d.com",
    "浙江晶盛机电股份有限公司": "www.jsjd.cc",
    "万向钱潮股份公司": "www.wxqc.com.cn",
    "万向智造有限公司": "www.wanxiang.com.cn",
    "新凤鸣集团股份有限公司": "www.xfmgroup.com",
    "华峰化学股份有限公司": "www.huafeng.com",
    "浙江我武生物科技股份有限公司": "www.wolwobiotech.com",
    "杭州老板电器股份有限公司": "www.robam.com",
    "杭州国芯微电子股份有限公司": "www.nationalchip.com",
    "杭州炬华科技股份有限公司": "www.sunrisemeter.com",
    "西子电梯科技有限公司": "www.xizielevator.com",
    "巨人通力电梯有限公司": "www.giantkone.com",
    "八环科技集团股份有限公司": "www.bahuan.com",
    "宁波钢铁有限公司": "www.ningbosteel.com.cn",
    "浙江田中精机股份有限公司": "www.tanac.com.cn",
    "浙江正泰电器股份有限公司": "www.chint.com",
    "浙江三花智能控制股份有限公司": "www.sanhuagroup.com",
    "浙江运达风电股份有限公司": "www.chinawindey.com",
    "浙江吉利控股集团有限公司": "www.geely.com",
    "零跑汽车有限公司": "www.leapmotor.com",
    "浙江大华智联有限公司": "www.dahuatech.com",
    "杭州汽轮机股份有限公司": "www.htc.cn",
    "浙江杭氧股份有限公司": "www.hangyang.com",
    "浙江苏泊尔股份有限公司": "www.supor.com.cn",
    "浙江爱仕达电器股份有限公司": "www.asd.com.cn",
    "浙江伟星实业发展股份有限公司": "www.weixing.com",
    "浙江奥康鞋业股份有限公司": "www.aokang.cn",
    "浙江红蜻蜓鞋业股份有限公司": "www.cnhqt.com",
    "浙江报喜鸟服饰股份有限公司": "www.baoxiniao.com.cn",
    "浙江森马服饰股份有限公司": "www.semir.com",
    "浙江乔治白服饰股份有限公司": "www.giuseppe.cn",
    "浙江永强集团股份有限公司": "www.yotrio.com",
    "浙江盾安人工环境股份有限公司": "www.dunan.com",
    "浙江亚太机电股份有限公司": "www.apg.cn",
    "浙江银轮机械股份有限公司": "www.yinlun.cn",
    "浙江双环传动机械股份有限公司": "www.shuanghuan.com",
    "浙江横店集团东磁股份有限公司": "www.dmegc.com",
    "浙江阳光照明电器集团股份有限公司": "www.yankon.com",
    "浙江龙盛集团股份有限公司": "www.longsheng.com",
    "浙江闰土股份有限公司": "www.runtu.com",
    "浙江新和成股份有限公司": "www.cnhu.com",
    "浙江医药股份有限公司": "www.chinazhuyi.com",
    "浙江华海药业股份有限公司": "www.huahaipharm.com",
    "浙江海正药业股份有限公司": "www.hisunpharm.com",
    "浙江仙琚制药股份有限公司": "www.xjpharma.com",
    "浙江京新药业股份有限公司": "www.jingxinpharm.com",
    "浙江普洛药业股份有限公司": "www.apeloa.com",
    "浙江康恩贝制药股份有限公司": "www.conbagroup.com",
    "浙江佐力药业股份有限公司": "www.zuoli.com",
    "浙江天能电池集团有限公司": "www.tianneng.com",
    "超威电源集团有限公司": "www.chaowei.com",
    "浙江南都电源动力股份有限公司": "www.naradapower.com",
    "浙江晶科能源有限公司": "www.jinkosolar.com",
    "浙江爱旭太阳能科技有限公司": "www.aikosolar.com",
    "浙江锦浪科技股份有限公司": "www.ginlong.com",
    "浙江昱能科技股份有限公司": "www.apsystems.com",
    "浙江德业科技股份有限公司": "www.deye.com",
    "浙江东方日升新能源股份有限公司": "www.risenenergy.com",
    "浙江钱江摩托股份有限公司": "www.qjmotor.com",
    "德力西电气有限公司": "www.delixi.com",
    "人民电器集团有限公司": "www.chinapeople.com",
    "天正电气股份有限公司": "www.tengen.com",
    "浙江奥克斯空调股份有限公司": "www.auxgroup.com",
    "浙江方太厨具有限公司": "www.fotile.com",
    "浙江美大实业股份有限公司": "www.meida.com",
    "浙江亿田智能厨电股份有限公司": "www.entive.com",
    "浙江帅丰电器股份有限公司": "www.sfjn.com",
    "浙江火星人厨具有限公司": "www.marssenger.com",
    "浙江星星冷链集成股份有限公司": "www.xingxing.com",
    "浙江古纤道绿色纤维有限公司": "www.guxiandao.com",
    "浙江海利得新材料股份有限公司": "www.hailide.com",
    "浙江尤夫高新纤维股份有限公司": "www.unifull.com",
    "浙江卫星石化股份有限公司": "www.satlpec.com",
    "浙江伟明环保装备集团有限公司": "www.cnweiming.com",
    "伟明环保装备集团有限公司": "www.cnweiming.com",
    "浙江菲达环保科技股份有限公司": "www.feida.com.cn",
    "浙江大立科技股份有限公司": "www.dali-tech.com",
    "杭州远方光电信息股份有限公司": "www.everfine.net",
    "杭州海康微影传感科技有限公司": "www.hikmicrotech.com",
    "浙江中控技术股份有限公司": "www.supcontech.com",
    "浙江华友钴业股份有限公司": "www.huayou.com",
    "浙江仙鹤股份有限公司": "www.xianhe.com",
    "杭州华三通信技术有限公司": "www.h3c.com",
    "浙江盾安精工集团有限公司": "www.dunanjg.com",
    "浙江富春江通信集团有限公司": "www.fcjt.com",
    "浙江航民股份有限公司": "www.hangmin.com",
    "浙江天圣控股集团有限公司": "www.tiansheng-group.com",
}


def cache_domain_file(kind: str, value: str) -> Path:
    from enrich_companies import cache_path

    return cache_path(kind, value)


def core_name(name: str) -> str:
    """Company name with the leading place name and corporate suffix removed."""
    core = name
    for prefix in sorted(CITY_PREFIXES, key=len, reverse=True):
        if core.startswith(prefix):
            core = core[len(prefix):]
            break
    for suffix in SUFFIXES:
        if core.endswith(suffix):
            core = core[: -len(suffix)]
            break
    return core.strip("（()） ")


def pinyin_variants(name: str) -> list[str]:
    """Plausible brand strings for a company name, most likely first."""
    if lazy_pinyin is None:
        return []
    core = core_name(name)
    if not core:
        return []

    stripped = core
    for word in GENERIC_WORDS:
        stripped = stripped.replace(word, "")
    py = lazy_pinyin(core)
    out: list[str] = ["".join(py)]
    if stripped and stripped != core:
        out.append("".join(lazy_pinyin(stripped)))
    out.append("".join(word[0] for word in py if word))
    if len(py) >= 2:
        out.append("".join(py[:2]))
        out.append("".join(word[0] for word in py[:3]))
    if len(py) >= 3:
        out.append("".join(py[:3]))
    if stripped:
        spy = lazy_pinyin(stripped)
        if len(spy) >= 2:
            out.append("".join(spy[:2]))
            out.append("".join(word[0] for word in spy))

    seen: set[str] = set()
    result: list[str] = []
    for value in out:
        value = re.sub(r"[^a-z0-9]", "", value.lower())
        if 3 <= len(value) <= 24 and value not in seen:
            seen.add(value)
            result.append(value)
    return result[:7]


def candidate_urls(name: str, seeds: dict[str, str]) -> list[str]:
    """Candidate site URLs in priority order (most likely first)."""
    hosts: list[str] = []
    for source in (BRAND_DOMAINS.get(name), seeds.get(name)):
        if source:
            host = re.sub(r"^https?://", "", source).strip("/")
            if host and host not in hosts:
                hosts.append(host)
    for variant in pinyin_variants(name):
        for tld in ("com", "cn", "com.cn"):
            host = f"{variant}.{tld}"
            if host not in hosts:
                hosts.append(host)
    return [f"https://{host}/" for host in hosts if not is_blocked(host)]


def resolves(host: str) -> bool:
    try:
        socket.getaddrinfo(host, None, proto=socket.IPPROTO_TCP)
        return True
    except OSError:
        return False


def strip_scheme(url: str) -> str:
    return re.sub(r"^https?://", "", url).strip("/").split("/")[0]


def verify(url: str, name: str, timeout: int = 14) -> dict | None:
    """Fetch a candidate and accept it only when the page proves the company.

    The acceptance rule itself lives in :func:`accepted` (currently rule v4); this
    wrapper only handles the cache. Cache reuse is version-aware: a hit only counts
    under the exact version that produced it, and negatives are reused across
    versions only when :data:`NEGATIVE_REUSE_VERSIONS` says the current rule is
    no looser than that version.
    """
    cached = read_cache("verify", strip_scheme(url))
    if cached is not None:
        version = cached.get("rule_version")
        if version == RULE_VERSION:
            return cached if cached.get("url") and cached.get("company") == name else None
        # 跨版本的「命中」必须重新抓取判定。
        #
        # 跨版本的**否定结论**只在当前规则不比写缓存时更宽松时才可以复用
        # （更严的规则只会减少命中）。v4 放宽了规则，所以这个集合是空的 ——
        # 显式写出来是为了让后续再放宽规则的人必须重新评估，而不是默认复用。
        if cached.get("url") is None and version in NEGATIVE_REUSE_VERSIONS:
            return None
    return classify(url, name, timeout=timeout)[1]


def accepted(html: str, title: str, name: str) -> str:
    """Return the evidence string when the page proves it is the company's own site.

    Acceptance (rule v4) — three independent paths, in order:

    ``title``
        The company's **full registered name** appears in the page title.
    ``copyright``
        The full name appears within :data:`COPYRIGHT_WINDOW` chars after a
        copyright / ICP-备案 marker, i.e. the page declares this company as its
        own 主办单位 / 版权主体.
    ``body-selfid``
        The full name appears in the visible body text in a **self-identifying
        frame** ("…有限公司是一家专业从事…"), and the title does not declare a
        *different* company. Added in v4 because real corporate sites often put
        the legal name only in their intro text while the title and copyright
        line carry just the brand.

    Findings that drove each tightening step, from auditing the matches:

    * ``中南仪表有限公司`` matched 南阳市中南仪表有限责任公司 — an unrelated company;
      a "distinctive key in the title" test (v1) accepted it. Rejected since v2.
    * ``杭州信多达智能科技有限公司`` matched 杭州信多达*电器*有限公司's site
      (xinduoda.com), where the full name appears only inside the address line
      ("地址：…，杭州信多达智能科技有限公司 联系电话：…"). Rejected by the
      ``body-selfid`` frame test v4 introduced, and by the different-company
      title guard (that title reads 杭州信多达电器有限公司).
    * ``华峰化学股份有限公司`` matched the parent 华峰集团 site, which never names
      the subsidiary with a self-identifying frame. Still rejected in v4.
    * ``杭州惠耳听力技术设备有限公司`` was rejected by v3 even though huier.org
      says "杭州惠耳听力技术设备有限公司是一家专业从事助听器研发…" — its title and
      copyright line only carry the brand 惠耳听力. Recovered by v4.
    * ``巨人通力电梯有限公司`` was rejected by v3 for the same reason
      (giantkone.com); also recovered by v4.

    Precision is deliberately favoured over recall: a directory that labels these
    records 官方认定 cannot afford wrong attributions, and anything rejected here
    can be filled by hand instead.
    """
    if name in title:
        return "title"
    for marker in COPYRIGHT_MARKERS.finditer(html):
        segment = html[marker.start(): marker.start() + COPYRIGHT_WINDOW]
        if name in segment:
            return "copyright"
    if _body_self_identifies(html, title, name):
        return "body-selfid"
    return ""


def _body_self_identifies(html: str, title: str, name: str) -> bool:
    """True when the page introduces *this* company in its own visible text."""
    others = {other for other in OTHER_COMPANY.findall(title) if other != name}
    if others:
        return False
    text = re.sub(r"\s+", " ", re.sub("<[^>]+>", " ", SCRIPT_STYLE.sub(" ", html)))
    for match in re.finditer(re.escape(name), text):
        following = JOINERS.sub("", text[match.end(): match.end() + 20])
        if SELF_ID_FRAMES.match(following):
            return True
    return False


def classify(url: str, name: str, timeout: int = VERIFY_TIMEOUT) -> tuple[str, dict | None]:
    """Fetch a candidate and say *why* it failed: ``rejected`` vs ``unreachable``.

    ``verify()`` collapses both failures into ``None``, which is fine for
    discovery (either way, keep looking) but wrong for re-auditing: a site that is
    merely down must not be demoted to 「查不到」. So the distinction has to survive
    out of the fetch layer.

    抓不到**不写缓存**。这跟「空搜索结果不写缓存」是同一条原则：站点临时挂掉、
    超时、返回 204 都属于「没有结论」，把没有结论固化成否定结论，下次就再也不会
    重试它了。
    """
    host = strip_scheme(url)
    reachable = False
    for scheme in ("https", "http"):
        target = f"{scheme}://{host}/"
        try:
            status, final_url, html = http_get(target, timeout=timeout)
        except Exception:
            continue
        if status >= 400 or not html:
            continue
        reachable = True

        title = ""
        title_match = re.search(r"<title[^>]*>(.*?)</title>", html, re.S | re.I)
        if title_match:
            title = re.sub(r"\s+", " ", re.sub("<[^>]+>", "", title_match.group(1))).strip()

        evidence = accepted(html, title, name)
        if not evidence:
            continue

        contacts = extract_contacts(html, final_url, host)
        meta = {
            "url": final_url,
            "host": host,
            "company": name,
            "title": title,
            "matched_token": name,
            "rule_version": RULE_VERSION,
            "evidence": evidence,
            "name_count": html.count(name),
            "checked_at": now_iso(),
            **contacts,
        }
        write_cache("verify", host, meta)
        return "accepted", meta

    if reachable:
        write_cache(
            "verify",
            host,
            {"host": host, "url": None, "company": name, "rule_version": RULE_VERSION, "checked_at": now_iso()},
        )
        return "rejected", None
    return "unreachable", None


def verify_first(live: list[str], name: str, timeout: int = VERIFY_TIMEOUT, workers: int = VERIFY_WORKERS) -> tuple[str, dict] | None:
    """Verify candidates concurrently, then return the highest-priority match.

    These fetches go to the *candidate companies' own sites*, not to a search
    engine, so raising the concurrency here does not make rate limiting any worse
    — it only removes local waiting. Verification used to be sequential with a
    timeout each, which dominated the runtime of a full pass.

    并发是为了省时间，但**结果仍按候选原始顺序**选取：优先级排序比「谁先返回」
    更有意义，否则同一家企业每次跑出来的官网可能都不一样。
    """
    if not live:
        return None
    with cf.ThreadPoolExecutor(max_workers=min(workers, len(live))) as pool:
        metas = list(pool.map(lambda url: verify(url, name, timeout=timeout), live))
    for url, meta in zip(live, metas):
        if meta:
            return url, meta
    return None


def dns_ok(urls: list[str]) -> list[str]:
    hosts = {strip_scheme(u): u for u in urls}
    alive: set[str] = set()
    with cf.ThreadPoolExecutor(max_workers=32) as pool:
        futures = {pool.submit(resolves, host): host for host in hosts}
        for future in cf.as_completed(futures):
            if future.result():
                alive.add(futures[future])
    return [u for u in urls if strip_scheme(u) in alive]


def discover_one(company: dict, seeds: dict[str, str], timeout: int = VERIFY_TIMEOUT) -> dict:
    name = company["name"]
    # 页面里出现的是**登记名**，不是带股票代码/品牌名括注的展示名。
    # 拿展示名（「科大讯飞股份有限公司（002230）」）去页面里找等于系统性漏检 ——
    # 第四批代表企业清单里 89/161 家带这类括注。
    match = company.get("match_name") or name
    record = dict(company)
    record.setdefault("enrich_status", "pending")

    candidates = candidate_urls(match, seeds)
    live = dns_ok(candidates)
    hit = verify_first(live, match, timeout=timeout)
    if hit:
        url, meta = hit
        record.update(
            website=meta["url"],
            public_phone=meta.get("public_phone", ""),
            public_email=meta.get("public_email", ""),
            source_url=meta["url"],
            verified_at=meta["checked_at"],
            site_title=meta.get("title", ""),
            enrich_status="verified",
            discovery_method=(
                "curated_domain" if strip_scheme(url) in {strip_scheme(BRAND_DOMAINS.get(name, "")), strip_scheme(seeds.get(name, ""))} else "pinyin_guess"
            ),
            candidates_tried=live.index(url) + 1,
        )
        return record

    record["enrich_status"] = "no_site_found" if live else "no_candidate"
    record["verified_at"] = now_iso()
    record["candidates_tried"] = len(live)
    return record


def reextract_contacts(results: dict[str, dict]) -> int:
    """Re-fetch accepted sites and re-extract only contact fields.

    Needed after hardening the extractor: the stored values came from pages whose
    script blocks were still being scanned (which is how a UUID fragment once
    became a phone number). The site itself already passed acceptance, so only the
    contact columns need refreshing.
    """
    refreshed = 0
    for name, record in results.items():
        if record.get("enrich_status") != "verified":
            continue
        url = record.get("website")
        if not url:
            continue
        try:
            status, final_url, html = http_get(url, timeout=14)
        except Exception:
            continue
        if status >= 400 or not html:
            continue
        contacts = extract_contacts(html, final_url, strip_scheme(url))
        before = (record.get("public_phone", ""), record.get("public_email", ""))
        record["public_phone"] = contacts["public_phone"]
        record["public_email"] = contacts["public_email"]
        if (record["public_phone"], record["public_email"]) != before:
            refreshed += 1
            print(
                f"  contacts updated: {name}\n"
                f"      phone {before[0] or '-'} -> {record['public_phone'] or '-'}\n"
                f"      email {before[1] or '-'} -> {record['public_email'] or '-'}",
                flush=True,
            )
    return refreshed


def revalidate(results: dict[str, dict]) -> tuple[int, int]:
    """Re-apply the current acceptance rule to every record already marked verified.

    Records carried over from ``enriched.json`` (or written by an older rule) must
    not bypass the gate — that is how a wrong attribution survives a bug fix.
    Returns (kept, demoted).

    **抓不到 ≠ 不通过。** 只有页面确实取到了、但不再能证明归属时才降级；
    站点临时挂了（超时 / 204 / 连接失败）只标 `recheck_needed`，保留原结论。
    否则一次网络抖动就会把好数据删掉，而这是最难被发现的一类数据损坏。
    """
    kept = demoted = 0
    unreachable = 0
    for name, record in list(results.items()):
        if record.get("enrich_status") != "verified":
            continue
        url = record.get("website") or record.get("source_url")
        if not url:
            record["enrich_status"] = "no_site_found"
            record.pop("website", None)
            demoted += 1
            continue
        status, meta = classify(url, name)
        if status == "unreachable":
            unreachable += 1
            record["recheck_needed"] = True
            record["recheck_reason"] = "复验时站点不可达，无法判定，保留原结论"
            print(f"  unreachable (保留原结论，待复检): {name} {url}", flush=True)
            continue
        if meta:
            record.update(
                website=meta["url"],
                public_phone=meta.get("public_phone", ""),
                public_email=meta.get("public_email", ""),
                source_url=meta["url"],
                verified_at=meta["checked_at"],
                site_title=meta.get("title", ""),
                enrich_status="verified",
                rule_version=RULE_VERSION,
            )
            # 这次复验通过了，把上一次「不可达待复检」的标记清掉
            record.pop("recheck_needed", None)
            record.pop("recheck_reason", None)
            kept += 1
        else:
            record.pop("website", None)
            record["public_phone"] = ""
            record["public_email"] = ""
            record["enrich_status"] = "failed_revalidation"
            record["verified_at"] = now_iso()
            demoted += 1
            print(f"  demoted (未通过当前验收规则): {name} {url}", flush=True)
    if unreachable:
        print(f"  {unreachable} 条因站点不可达而保留原结论（未降级）", flush=True)
    return kept, demoted


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--only", action="append", default=[])
    parser.add_argument(
        "--list-name",
        default="",
        help="只处理某个来源名单里的企业（按名单名模糊匹配）。"
        "分批跑单个名单比全量快得多，也便于看清某一批的命中率。",
    )
    parser.add_argument("--measure", type=int, default=0, help="sample size, report only")
    parser.add_argument("--refresh", action="store_true", help="redo companies already verified")
    parser.add_argument(
        "--revalidate",
        action="store_true",
        help="只把当前验收规则重新套用到已标记 verified 的记录上，不抓新候选",
    )
    parser.add_argument(
        "--recontacts",
        action="store_true",
        help="重新抓取已 verified 的站点，只刷新联系方式字段（抽取规则加固后使用）",
    )
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument(
        "--timeout",
        type=int,
        default=VERIFY_TIMEOUT,
        help="单站抓取超时（秒）。中小企业站点普遍很慢，但超过这个秒数的站点也不是可靠线索，"
        "而且全量跑批的耗时基本由它决定。",
    )
    parser.add_argument("--out", default=str(OUT))
    args = parser.parse_args()

    rows = json.loads(OUT.read_text(encoding="utf-8")) if OUT.exists() else []
    results = {r["name"]: r for r in rows}
    # `results` 是**整个文件**读进来的，所以 `--list-name` 只缩小 todo，不会
    # 把别的名单的结果丢掉 —— 但 `--out` 必须指向同一个文件，否则会写出一份
    # 「只有这个名单」的文件，看起来像其它名单的官网全没了。
    # 保留既有成果：已核实的官网/联系方式不重做（除非 --refresh）
    known = {c["name"] for c in load_companies()}
    enriched = ROOT / "data" / "curated" / "enriched.json"
    if enriched.exists():
        for row in json.loads(enriched.read_text(encoding="utf-8")):
            if row["name"] in known and row.get("enrich_status") == "verified":
                results.setdefault(row["name"], row)
    seeds = load_seed()

    companies = [c for c in load_companies() if c["name"] in known]

    if args.revalidate:
        kept, demoted = revalidate(results)
        save(results, args.out)
        print(f"revalidate done: kept={kept} demoted={demoted}")
        return

    if args.recontacts:
        changed = reextract_contacts(results)
        save(results, args.out)
        print(f"recontacts done: {changed} record(s) changed")
        return

    todo = [
        c
        for c in companies
        if (not args.only or any(k in c["name"] for k in args.only))
        and (not args.list_name or any(args.list_name in n for n in c["list_names"]))
    ]
    if not args.refresh:
        todo = [c for c in todo if results.get(c["name"], {}).get("enrich_status") != "verified"]
    if args.limit:
        todo = todo[: args.limit]
    if args.measure:
        todo = todo[: args.measure]

    print(f"companies={len(companies)} already_verified={sum(1 for r in results.values() if r.get('enrich_status') == 'verified')} todo={len(todo)}", flush=True)
    started = time.time()

    with cf.ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(discover_one, company, seeds, args.timeout): company for company in todo}
        for index, future in enumerate(cf.as_completed(futures), 1):
            company = futures[future]
            try:
                record = future.result()
            except Exception as exc:
                record = dict(company, enrich_status="error", error=type(exc).__name__, verified_at=now_iso())
            results[company["name"]] = record
            flag = "OK " if record.get("enrich_status") == "verified" else " -- "
            print(
                f"[{index}/{len(todo)}] {flag} {record.get('enrich_status',''):13s} "
                f"{record['name'][:26]:28s} {record.get('website','')} {record.get('public_phone','')}",
                flush=True,
            )
            if index % 25 == 0 and not args.measure:
                save(results, args.out)

    verified = sum(1 for r in results.values() if r.get("enrich_status") == "verified")
    print(f"\ndone in {time.time()-started:.0f}s: {len(results)} records, {verified} verified")

    if args.measure:
        hits = [r for r in results.values() if r.get("enrich_status") == "verified"]
        print(f"measure hit rate: {len(hits)}/{len(todo)} = {100*len(hits)/max(1,len(todo)):.0f}%")
        for r in sorted(hits, key=lambda x: x["name"]):
            print(f"   {r['name'][:24]:26s} {r.get('website','')[:44]:46s} {r.get('discovery_method','')}")
        return

    save(results, args.out)


def save(results: dict[str, dict], path: str) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(
        json.dumps(sorted(results.values(), key=lambda r: r["name"]), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()