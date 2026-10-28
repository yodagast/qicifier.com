"""Web-search-driven official-site discovery (Playwright + 中文搜索引擎).

Why a browser: raw HTTP to every search engine is blocked in this environment
(Bing serves a bot-challenge, DuckDuckGo/Mojeek/Brave/Ecosia/Startpage/Yandex
reject or captcha headless clients — see memory.md §3.3). Driving a real browser
works, but not all engines do:

    Baidu   结果页可加载，但结果块选择器不稳定
    Sogou   可用，域名混在摘要文本里（需正则捞出）
    360     可用，且 ``cite`` 直接给出结果域名 —— 首选

Pipeline (search results are only *candidates*; the site itself must still pass
the same acceptance rule as the pinyin path, so precision is unchanged):

    search "<name> 官网"   →  result domains (most likely first)
                            →  DNS 预筛
                            →  classify() 强验收（规则 v4）
                            →  联系方式抽取

Results are cached per company, so a run can be interrupted and resumed.

**Rate limits are the binding constraint, not the code.** Measured behaviour: after
roughly 270 searches the engines start returning empty pages / 「访问异常页面」 /
安全验证. So this tool defaults to a deliberately slow pace and pauses itself when it
detects a run of empty results. Treat it as a multi-day background job, or supply a
commercial search API for real throughput. Do **not** try to evade the block
(rotating UAs / proxies) — slow down instead, and never cache empty results as if
they were a real finding.

Usage::

    python tools/search_discover.py --limit 20                 # 小批量试跑
    python tools/search_discover.py                            # 补齐未核实企业（默认慢速）
    python tools/search_discover.py --delay 3 --jitter 2       # 更激进（容易被限流）
    python tools/search_discover.py --only 天能 --refresh
"""

from __future__ import annotations

import argparse
import json
import random
import re
import sys
import time
import urllib.parse
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from companies import load_companies  # noqa: E402
from discover_sites import (  # noqa: E402
    RULE_VERSION,
    candidate_urls,
    dns_ok,
    load_seed,
    now_iso,
    read_cache,
    strip_scheme,
    verify_first,
    write_cache,
)
from enrich_companies import is_blocked  # noqa: E402

OUT = ROOT / "data" / "curated" / "discovered.json"

# 单企业最多校验多少个候选（按搜索相关性排序后取前 N）
MAX_VERIFY_CANDIDATES = 6
# 单站抓取超时：中国中小企业站点常很慢，但超过 9s 的站点也不是可靠线索
VERIFY_TIMEOUT = 9
SEARCH_CACHE = "search360"
# 搜索结果摘要（含聚合站的电话/地址），供 tools/directory_contacts.py 离线复用
BLOCK_CACHE = "searchblock"

UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)

# 结果里要排除的域名（聚合站/地图/百科/招聘/工商查询，不是企业官网）
BLOCKED_EXTRA = (
    "so.com", "360.cn", "baidu.com", "sogou.com", "bing.com", "qq.com",
    "tianyancha.com", "qcc.com", "qichacha.com", "aiqicha.baidu.com", "gsxt.gov.cn",
    "zhipin.com", "zhaopin.com", "58.com", "liepin.com", "51job.com", "docin.com",
    "doc88.com", "wenku.baidu.com", "baike.baidu.com", "wikipedia.org", "zhihu.com",
    "weibo.com", "douyin.com", "xiaohongshu.com", "1688.com", "alibaba.com",
    "made-in-china.com", "hc360.com", "china.cn", "b2b168.com", "11467.com",
    "yjbysk.com", "gongchang.com", "chazidian.com", "shuidi.cn", "huangye88.com",
    "cn.made-in-china.com", "bossgoo.com", "globalsources.com", "tradekey.com",
    "douban.com", "bilibili.com", "toutiao.com", "sina.com.cn", "sohu.com",
    "163.com", "ifeng.com", "thepaper.cn", "jd.com", "tmall.com", "taobao.com",
    "gov.cn", "creditchina.gov.cn", "amr.zj.gov.cn", "zj.gov.cn", "jobui.com",
    "cbi360.net", "chinabidding.cn", "qianzhan.com", "marketwatch.com",
)

DOMAIN_RE = re.compile(
    r"(?:https?://)?((?:[a-z0-9]|\*)+(?:-[a-z0-9]+)*\.(?:com|cn|net|org|cc|vip|top|xin|info|biz|shop|cn\.com|com\.cn|net\.cn|org\.cn))",
    re.I,
)


def is_bad_domain(host: str) -> bool:
    host = host.lower()
    if is_blocked(host):
        return True
    return any(host == d or host.endswith("." + d) for d in BLOCKED_EXTRA)


def domain_of(text: str) -> str:
    """Pull the first registrable domain out of a cite/snippet string."""
    if not text:
        return ""
    match = DOMAIN_RE.search(text.replace(" ", ""))
    if not match:
        return ""
    host = match.group(1).lower().lstrip("*.").replace("cn.com", "com.cn")
    if "." not in host or is_bad_domain(host):
        return ""
    return host


def search_domains(page, name: str, force_fetch: bool = False) -> tuple[list[str], list[str], bool]:
    """Search for a company's official site.

    Returns ``(candidate domains, raw result blocks, from_cache)``.

    The raw blocks are kept because they are the only cheap source of contact
    details for companies that have no verifiable site of their own: 顺企网 /
    黄页88 style aggregators put a switchboard number and an address right in the
    snippet. Re-reading them costs no extra search request, which is the scarce
    resource here.

    ``from_cache`` lets the caller skip the politeness delay: that delay exists to
    protect the search engine, and a cache hit never touches it.

    ``force_fetch`` re-scrapes even when the domains are already cached. That is
    only needed because the摘要 cache was added later than the domain cache —
    without it, every company searched before the upgrade would keep its domains
    and permanently lack snippets.
    """
    cached = read_cache(SEARCH_CACHE, name)
    blocks_cached = read_cache(BLOCK_CACHE, name)
    if cached is not None and (blocks_cached is not None or not force_fetch):
        return cached, blocks_cached or [], True

    query = urllib.parse.quote(f"{name} 官网")
    domains: list[str] = []
    blocks: list[str] = []

    def harvest(url: str, extract_js: str) -> None:
        try:
            page.goto(url, timeout=14000, wait_until="domcontentloaded")
            page.wait_for_timeout(1100)
            values = page.evaluate(extract_js)
        except Exception:
            return
        for value in values:
            text = re.sub(r"\s+", " ", (value or "")).strip()
            if not text:
                continue
            blocks.append(text)
            host = domain_of(text)
            if host and host not in domains:
                domains.append(host)

    # 首选 360：cite 直接给出结果域名
    harvest(
        f"https://www.so.com/s?q={query}&rn=20",
        """() => [...document.querySelectorAll('h3')].map(h =>
             (h.closest('li')||h.closest('div.res-list')||h.parentElement||h).innerText || '')""",
    )
    # 兜底 Sogou / Baidu（只在 360 完全没结果时）
    if not domains:
        harvest(
            f"https://www.sogou.com/web?query={query}",
            """() => [...document.querySelectorAll('.vrwrap, .rb, .result')]
                     .map(el => el.innerText || '')""",
        )
    if not domains:
        harvest(
            f"https://www.baidu.com/s?wd={query}&rn=20",
            """() => [...document.querySelectorAll('div.result, div.c-container, div.result-op')]
                     .map(el => el.innerText || '')""",
        )

    # 空结果**不写缓存**：可能是真的没有结果，也可能是被限流了。
    # 区分不了就不缓存，宁可下次重查，也不要把限流污染固化下来。
    # 摘要用同一个条件缓存：判定不了就别把它当结论。
    if domains:
        write_cache(SEARCH_CACHE, name, domains[:8])
        write_cache(BLOCK_CACHE, name, blocks[:20])
    elif cached:
        # 这次没抓到结果（很可能是被限流）——**旧域名不能因此作废**。
        # 空结果是不确定信号，不是「这家企业没有官网」的结论。
        domains = cached
    return domains[:8], blocks[:20], False


class RateLimited(RuntimeError):
    """连续多次搜索都拿不到结果 —— 判定被搜索引擎限流，应暂停退避。"""


def discover_with_search(company: dict, seeds: dict[str, str], page, force_fetch: bool = False) -> dict:
    name = company["name"]
    # 与 discover_sites 一致：验收用登记名，展示用原名的全称
    match = company.get("match_name") or name
    record = dict(company)
    record.setdefault("enrich_status", "pending")

    # 候选 = 搜索域名 → 站方清单/品牌表/拼音变体（搜索排前面，命中率最高）
    search_hosts, _blocks, _cached = search_domains(page, match, force_fetch=force_fetch)
    urls = [f"https://{h}/" for h in search_hosts]
    for url in candidate_urls(match, seeds):
        if strip_scheme(url) not in {strip_scheme(u) for u in urls}:
            urls.append(url)

    # 搜索已按相关性排序，只校验最可能的前 N 个：候选越多，逐个抓取的成本越高，
    # 而真正的官网几乎总在前几条里。
    live = dns_ok(urls[:MAX_VERIFY_CANDIDATES])
    hit = verify_first(live, match, timeout=VERIFY_TIMEOUT)
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
            discovery_method="web_search" if strip_scheme(url) in search_hosts else "curated_or_pinyin",
            search_hits=len(search_hosts),
            search_cached=_cached,
        )
        return record

    record["enrich_status"] = "no_site_found" if live else "no_candidate"
    # 否定结论也要写规则版本：续跑时靠它判断「这个结论还算不算数」
    record["rule_version"] = RULE_VERSION
    record["verified_at"] = now_iso()
    record["search_hits"] = len(search_hosts)
    record["search_cached"] = _cached
    return record


def is_retired(record: dict) -> bool:
    """这条记录已经**在本版规则下**查过、且搜索有缓存 → 重跑只会得到同样的结论。

    没有这个判断，每次中断续跑都会把几百家已经查过的企业再走一遍
    （搜索命中缓存不发请求，但 DNS 预筛和候选校验仍会跑满一轮），
    续跑成本高到让人不敢重启 —— 而限流场景下**必须能随时重启**。

    两个前提缺一不可：
    * ``rule_version`` 必须等于当前版本（规则一改，旧结论就作废，见 §3.4c）；
    * 搜索结果必须有缓存（没有缓存说明当时可能被限流，结论不可信）。
    """
    if record.get("enrich_status") != "no_site_found":
        return False
    if record.get("rule_version") != RULE_VERSION:
        return False
    return read_cache(SEARCH_CACHE, record["name"]) is not None


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--only", action="append", default=[])
    parser.add_argument("--refresh", action="store_true", help="重做已核实的记录")
    parser.add_argument(
        "--delay",
        type=float,
        default=10.0,
        help="每次搜索之间的基础间隔（秒）。默认刻意放慢：实测约 270 次请求后会被 360/Baidu 限流。",
    )
    parser.add_argument("--jitter", type=float, default=8.0, help="在基础间隔上叠加的随机抖动（秒）")
    parser.add_argument(
        "--empty-streak-limit",
        type=int,
        default=6,
        help="连续多少次搜索无结果就判定被限流并退避",
    )
    parser.add_argument("--cooldown", type=float, default=1800.0, help="限流后的退避秒数（默认 30 分钟）")
    parser.add_argument(
        "--refresh-blocks",
        action="store_true",
        help="只补摘要：对「有域名缓存但没有摘要缓存」的企业重新搜一次。"
        "摘要缓存是后加的，升级前查过的企业要跑这个才拿得到联系方式兜底层的输入。",
    )
    parser.add_argument("--out", default=str(OUT))
    args = parser.parse_args()

    from playwright.sync_api import sync_playwright

    rows = json.loads(OUT.read_text(encoding="utf-8")) if OUT.exists() else []
    results = {r["name"]: r for r in rows}
    seeds = load_seed()
    companies = load_companies()

    todo = [c for c in companies if not args.only or any(k in c["name"] for k in args.only)]
    if args.refresh_blocks:
        # 只挑「域名已缓存、摘要缺失」的：已经补过摘要的不再重复搜索（限流是稀缺资源）
        todo = [
            c
            for c in todo
            if read_cache(SEARCH_CACHE, c["name"]) is not None and read_cache(BLOCK_CACHE, c["name"]) is None
        ]
    elif not args.refresh:
        todo = [
            c
            for c in todo
            if results.get(c["name"], {}).get("enrich_status") != "verified"
            and not is_retired(results.get(c["name"], {}))
        ]
    if args.limit:
        todo = todo[: args.limit]

    verified_now = sum(1 for r in results.values() if r.get("enrich_status") == "verified")
    print(f"companies={len(companies)} verified={verified_now} todo={len(todo)}", flush=True)
    started = time.time()

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(locale="zh-CN", user_agent=UA)
        page = context.new_page()
        empty_streak = 0
        for index, company in enumerate(todo, 1):
            try:
                record = discover_with_search(company, seeds, page, force_fetch=args.refresh_blocks)
            except Exception as exc:
                record = dict(company, enrich_status="error", error=type(exc).__name__, verified_at=now_iso())
            results[company["name"]] = record

            # 连续拿不到搜索结果 = 被限流。此时继续跑只会得到一堆假的「查不到」，
            # 而且这些空结果不该被当成结论，所以直接停下来等退避。
            if record.get("search_hits", 0) == 0 and record.get("enrich_status") != "verified":
                empty_streak += 1
            else:
                empty_streak = 0
            if empty_streak >= args.empty_streak_limit:
                save(results, args.out)
                print(
                    f"\n连续 {empty_streak} 次搜索无结果，判定被限流，暂停 {args.cooldown:.0f}s 后继续…",
                    flush=True,
                )
                time.sleep(args.cooldown)
                empty_streak = 0
            flag = "OK " if record.get("enrich_status") == "verified" else " -- "
            print(
                f"[{index}/{len(todo)}] {flag} {record.get('enrich_status',''):13s} "
                f"{record['name'][:26]:28s} hits={record.get('search_hits',0):2d} {record.get('website','')}",
                flush=True,
            )
            if index % 10 == 0:
                save(results, args.out)
            # 命中缓存 = 这一次没有向搜索引擎发出任何请求，没有需要「礼貌」的对象，
            # 所以不必等。真正的请求仍然按 --delay/--jitter 放慢。
            if not record.get("search_cached"):
                time.sleep(max(args.delay, 0) + random.uniform(0, max(args.jitter, 0)))
        browser.close()

    save(results, args.out)
    verified = sum(1 for r in results.values() if r.get("enrich_status") == "verified")
    print(f"\ndone in {time.time()-started:.0f}s: {len(results)} records, {verified} verified")


def save(results: dict[str, dict], path: str) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(
        json.dumps(sorted(results.values(), key=lambda r: r["name"]), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()