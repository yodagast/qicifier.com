"""Public-source enrichment for the Zhejiang enterprise directory.

Scope and boundaries (see README "数据边界"):

* Only **public** company contact channels are collected: the company's own
  official website, a landline switchboard / 400 number printed on that website,
  and a general mailbox (``mailto:``) published on that website.
* Mobile numbers (``1[3-9]xxxxxxxxx``) are actively filtered out — the directory
  never publishes an individual's contact details.
* Every recorded value carries ``source_url`` + ``verified_at`` so the UI can
  show how it was obtained. Values that cannot be verified stay empty rather than
  guessed.

Pipeline:
  1. candidate discovery  — search engine (360 so.com) result links for
     "<company> 官网", redirects resolved to final domains.
  2. site verification    — fetch the candidate home page and require the
     company's distinctive name token to appear on it.
  3. contact extraction   — follow contact/about links and pull out 400/landline
     numbers and general mailboxes.

Results are cached per company under ``data/cache/`` and appended to
``data/curated/enriched.json`` so a run can be interrupted and resumed.

Usage::

    python tools/enrich_companies.py --limit 50
    python tools/enrich_companies.py --only 浙江大华技术股份有限公司
    python tools/enrich_companies.py            # everything not yet attempted
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import random
import re
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE_DIR = ROOT / "data" / "source"
CACHE_DIR = ROOT / "data" / "cache"
OUT = ROOT / "data" / "curated" / "enriched.json"

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)
SSL_CTX = ssl.create_default_context()
SSL_CTX.check_hostname = False
SSL_CTX.verify_mode = ssl.CERT_NONE

COMPANY_SUFFIXES = (
    "集团股份有限公司", "股份有限公司", "集团有限公司", "有限公司",
    "科技集团", "控股集团", "研究院", "集团", "公司",
)
CITY_PREFIXES = (
    "杭州市", "宁波市", "温州市", "嘉兴市", "湖州市", "绍兴市", "金华市",
    "衢州市", "舟山市", "台州市", "丽水市", "浙江省", "浙江", "中国",
)
GENERIC_TOKENS = ("科技", "技术", "集团", "股份", "有限", "控股", "实业", "发展", "电子")

# 座机/400 号码：区号 3-4 位（0 开头），号码 7-8 位，分机用「-」分隔且最多 6 位。
# 严格结构 + normalize_phone() 二次校验，避免把统计数字/时间戳当成电话。
# 前后禁止出现 16 进制字符：UUID / 哈希里的片段（如
# f64e4e43-c1a7-4c2d-a027-0215656e237c 中的 027-0215656）长得像电话，
# 必须靠这个 lookaround 排除。
PHONE_CANDIDATE_RE = re.compile(
    r"(?<![0-9a-fA-F-])((?:400|800)[- ]?\d{3}[- ]?\d{4}|0(?:10|[1-9]\d{1,2})[- ]?\d{7,8}(?:[- ]?\d{1,6})?)(?![0-9a-fA-F])"
)
# 电话出现位置附近的关键词，用于优先选取有语境的号码
PHONE_CONTEXT_RE = re.compile(
    r"(?:电\s*话|联系\s*电话|总机|客服|Tel|TEL|Phone|contact\s*number)[^\d]{0,24}"
    r"((?:400|800)[- ]?\d{3}[- ]?\d{4}|0(?:10|[1-9]\d{1,2})[- ]?\d{7,8}(?:[- ]?\d{1,6})?)",
)
TEL_HREF_RE = re.compile(r'href=["\']tel:([^"\']+)["\']', re.I)
MOBILE_RE = re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)")
# 邮箱本地部分里的手机号可能带分隔符（138-1760-8043）
mo_strip = re.compile(r"[\s._-]")
MAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
# 抽取联系方式前先去掉脚本/样式：UUID、追踪 ID、埋点数据都在这里，是噪声的主要来源
SCRIPT_BLOCK_RE = re.compile(r"<(script|style|noscript)[^>]*>.*?</\1>", re.S | re.I)

# 有效长途区号（工信部公开号段；覆盖浙江 11 市与全国主要城市）
VALID_AREA_CODES = {
    "010", "020", "021", "022", "023", "024", "025", "027", "028", "029",
    "0310", "0311", "0312", "0313", "0314", "0315", "0316", "0317", "0318", "0319",
    "0335", "0349", "0350", "0351", "0352", "0353", "0354", "0355", "0356", "0357",
    "0358", "0359", "0370", "0371", "0372", "0373", "0374", "0375", "0376", "0377",
    "0379", "0391", "0392", "0393", "0394", "0395", "0396", "0398",
    "0410", "0411", "0412", "0413", "0414", "0415", "0416", "0417", "0418", "0419",
    "0421", "0427", "0429", "0431", "0432", "0433", "0434", "0435", "0436", "0437",
    "0438", "0439", "0440", "0451", "0452", "0453", "0454", "0455", "0456", "0457",
    "0458", "0459", "0464", "0467", "0468", "0469", "0470", "0471", "0472", "0473",
    "0474", "0475", "0476", "0477", "0478", "0479", "0482", "0483",
    "0510", "0511", "0512", "0513", "0514", "0515", "0516", "0517", "0518", "0519",
    "0523", "0527", "0530", "0531", "0532", "0533", "0534", "0535", "0536", "0537",
    "0538", "0539", "0543", "0546", "0550", "0551", "0552", "0553", "0554", "0555",
    "0556", "0557", "0558", "0559", "0561", "0562", "0563", "0564", "0565", "0566",
    "0570", "0571", "0572", "0573", "0574", "0575", "0576", "0577", "0578", "0579",
    "0580", "0591", "0592", "0593", "0594", "0595", "0596", "0597", "0598", "0599",
    "0701", "0710", "0711", "0712", "0713", "0714", "0715", "0716", "0717", "0718",
    "0719", "0722", "0724", "0728", "0730", "0731", "0732", "0733", "0734", "0735",
    "0736", "0737", "0738", "0739", "0743", "0744", "0745", "0746", "0750", "0751",
    "0752", "0753", "0754", "0755", "0756", "0757", "0758", "0759", "0760", "0762",
    "0763", "0766", "0768", "0769", "0770", "0771", "0772", "0773", "0774", "0775",
    "0776", "0777", "0778", "0779", "0790", "0791", "0792", "0793", "0794", "0795",
    "0796", "0797", "0798", "0799", "0810", "0812", "0813", "0816", "0817", "0818",
    "0825", "0826", "0827", "0830", "0831", "0832", "0833", "0834", "0835", "0836",
    "0837", "0838", "0839", "0851", "0852", "0853", "0854", "0855", "0856", "0857",
    "0858", "0859", "0860", "0870", "0871", "0872", "0873", "0874", "0875", "0876",
    "0877", "0878", "0879", "0883", "0886", "0887", "0888", "0890", "0891", "0892",
    "0893", "0894", "0895", "0896", "0897", "0898", "0899", "0901", "0902", "0903",
    "0906", "0908", "0909", "0910", "0911", "0912", "0913", "0914", "0915", "0916",
    "0917", "0919", "0930", "0931", "0932", "0933", "0934", "0935", "0936", "0937",
    "0938", "0939", "0940", "0941", "0943", "0951", "0952", "0953", "0954", "0955",
    "0957", "0961", "0962", "0963", "0966", "0970", "0971", "0972", "0973", "0974",
    "0975", "0976", "0977", "0979", "0980", "0981", "0982", "0983", "0990", "0991",
    "0992", "0993", "0994", "0995", "0996", "0997", "0998", "0999",
}

# 建站商 / 统计服务 / 占位符邮箱，以及示例域名，一律不算企业公开邮箱
BAD_MAIL_DOMAINS = (
    "example.com", "example.org", "domain.com", "yourdomain", "sentry.io",
    "300.cn", "wixpress.com", "godaddy.com", "cloudflare.com", "akamai",
    "test.com", "email.com", "company.com", "qq.com", "163.com", "126.com",
)


def normalize_phone(raw: str) -> str:
    """Validate a candidate number and return a display form, or '' when implausible."""
    digits = re.sub(r"\D", "", raw)
    if digits.startswith(("400", "800")):
        return f"{digits[:3]}-{digits[3:6]}-{digits[6:10]}" if len(digits) == 10 else ""
    if MOBILE_RE.fullmatch(digits):
        return ""  # 不采集个人手机号
    if len(digits) < 10 or len(digits) > 18:
        return ""
    # 分机只在原文有明确分隔符时才认（避免把网页时间戳/统计数字切成电话）
    has_extension_separator = bool(re.search(r"\d[- 转]\d{1,6}$", raw.strip()))
    for length in (4, 3):  # 4 位区号优先（如 0571），再退到 3 位（如 021）
        area, rest = digits[:length], digits[length:]
        if area not in VALID_AREA_CODES:
            continue
        if len(rest) in (7, 8):
            return f"{area}-{rest}"
        if has_extension_separator and 8 < len(rest) <= 12:
            return f"{area}-{rest[:8]}-{rest[8:]}"
    return ""
CONTACT_HINTS = ("contact", "lxwm", "lianxi", "about", "gywm", "guanyu", "contactus", "contact-us")
BLOCKED_DOMAINS = (
    "so.com", "360.cn", "baidu.com", "bing.com", "google.com", "sogou.com",
    "zhipin.com", "zhaopin.com", "58.com", "tianyancha.com", "qcc.com",
    "qichacha.com", "aiqicha.baidu.com", "gongsi.com", "b2b168.com", "1688.com",
    "alibaba.com", "made-in-china.com", "hc360.com", "china.cn", "youtube.com",
    "weibo.com", "zhihu.com", "douban.com", "wikipedia.org", "gov.cn", "gsxt.gov.cn",
    "linkedin.com", "facebook.com", "zhiye.com", "liepin.com", "51job.com",
)


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def cache_path(kind: str, key: str) -> Path:
    digest = hashlib.sha1(key.encode("utf-8")).hexdigest()[:16]
    return CACHE_DIR / kind / f"{digest}.json"


def read_cache(kind: str, key: str):
    path = cache_path(kind, key)
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return None
    return None


def write_cache(kind: str, key: str, value) -> None:
    path = cache_path(kind, key)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")


def http_get(url: str, timeout: int = 15, max_bytes: int = 400_000) -> tuple[int, str, str]:
    request = urllib.request.Request(
        url, headers={"User-Agent": USER_AGENT, "Accept-Encoding": "gzip", "Accept-Language": "zh-CN,zh;q=0.9"}
    )
    with urllib.request.urlopen(request, timeout=timeout, context=SSL_CTX) as response:
        body = response.read(max_bytes)
        if response.headers.get("Content-Encoding") == "gzip":
            try:
                body = gzip.decompress(body)
            except OSError:
                pass
        charset = response.headers.get_content_charset() or "utf-8"
        return response.status, response.geturl(), body.decode(charset, "ignore")


def name_tokens(name: str) -> list[str]:
    """Distinctive tokens used to recognise a company on its own website."""
    core = name
    for prefix in CITY_PREFIXES:
        if core.startswith(prefix):
            core = core[len(prefix):]
            break
    for suffix in COMPANY_SUFFIXES:
        if core.endswith(suffix):
            core = core[: -len(suffix)]
            break
    tokens = {core}
    if len(core) > 4:
        tokens.add(core[:4])
    stripped = core
    for token in GENERIC_TOKENS:
        stripped = stripped.replace(token, "")
    if len(stripped) >= 2:
        tokens.add(stripped)
    return sorted((t for t in tokens if len(t) >= 2), key=len, reverse=True)


def discover_candidates(name: str, provider: str = "so") -> list[str]:
    """Return candidate domains for a company, most likely first."""
    cache_key = f"{provider}:{name}"
    cached = read_cache("search", cache_key)
    if cached is not None:
        return cached

    query = urllib.parse.quote(f"{name} 官网")
    url = f"https://www.so.com/s?q={query}" if provider == "so" else f"https://www.bing.com/search?q={query}"

    domains: list[str] = []
    try:
        _, _, html = http_get(url, timeout=20)
    except Exception:
        write_cache("search", cache_key, domains)
        return domains

    for match in re.finditer(r"<h3[^>]*>\s*<a[^>]*href=\"([^\"]+)\"", html):
        href = match.group(1).replace("&amp;", "&")
        final = resolve_domain(href)
        if not final or final in domains or is_blocked(final):
            continue
        domains.append(final)
        if len(domains) >= 6:
            break

    write_cache("search", cache_key, domains)
    return domains


def resolve_domain(href: str) -> str | None:
    """Resolve a (possibly redirecting) search-result link to a registrable host."""
    if href.startswith("//"):
        href = "https:" + href
    if not href.startswith("http"):
        return None
    cached = read_cache("redirect", href)
    if cached is not None:
        return cached or None

    host = None
    try:
        request = urllib.request.Request(href, headers={"User-Agent": USER_AGENT}, method="GET")
        with urllib.request.urlopen(request, timeout=12, context=SSL_CTX) as response:
            host = urllib.parse.urlparse(response.geturl()).netloc.lower()
    except Exception:
        host = urllib.parse.urlparse(href).netloc.lower()

    host = host.split(":")[0]
    write_cache("redirect", href, host)
    return host


def same_site(host_a: str, host_b: str) -> bool:
    """True when two hosts belong to the same site, ignoring a leading ``www.``."""
    return host_a.removeprefix("www.") == host_b.removeprefix("www.")


def is_blocked(host: str) -> bool:
    return any(host == d or host.endswith("." + d) for d in BLOCKED_DOMAINS)


def parse_seed_date(meta: dict) -> datetime | None:
    value = meta.get("checked_at")
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def verify_site(host: str, name: str) -> dict | None:
    """Fetch a candidate site and confirm it belongs to the company."""
    cached = read_cache("site", host)
    if cached and not cached.get("url"):
        return None  # verified-negative, cached

    for scheme in ("https", "http"):
        base = f"{scheme}://{host}/"
        try:
            status, final_url, html = http_get(base, timeout=15)
        except Exception:
            continue
        if status >= 400 or not html:
            continue
        tokens = name_tokens(name)
        if not any(token in html for token in tokens[:2]):
            continue
        title = ""
        title_match = re.search(r"<title[^>]*>(.*?)</title>", html, re.S | re.I)
        if title_match:
            title = re.sub(r"\s+", " ", re.sub("<[^>]+>", "", title_match.group(1))).strip()
        contacts = extract_contacts(html, base, host)
        meta = {
            "url": final_url,
            "host": host,
            "title": title,
            "matched_token": next(t for t in tokens[:2] if t in html),
            "checked_at": now_iso(),
            **contacts,
        }
        write_cache("site", host, meta)
        return meta
    write_cache("site", host, {"host": host, "url": None, "checked_at": now_iso()})
    return None


def clean_email(value: str) -> str:
    """Reject placeholder / site-builder mailboxes and tracking-looking addresses."""
    lowered = value.lower().strip(".")
    if "@" not in lowered:
        return ""
    if any(bad in lowered for bad in BAD_MAIL_DOMAINS):
        return ""
    local, _, domain = lowered.partition("@")
    if not local or "." not in domain:
        return ""
    if len(local) > 48 or sum(ch.isdigit() for ch in local) > 12:
        return ""  # hashes / tracking ids, not a mailbox
    # 本地部分是**手机号**的邮箱（``13817608043@139.com``）：邮箱本身是公开的，
    # 但本地部分就是那个人手机号，展示它等于把个人号码印在页面上 ——
    # 与「手机号一律不采集、不展示」这条承诺直接冲突（而且是绕过承诺最容易的路径）。
    digits = mo_strip.sub("", local)
    if MOBILE_RE.fullmatch(local) or (digits.isdigit() and len(digits) == 11):
        return ""
    return lowered


def extract_public_email(html: str) -> str:
    """Prefer mailto: addresses; otherwise fall back to in-text mailboxes."""
    for match in re.finditer(r'href=["\']mailto:([^"\'?]+)', html, re.I):
        cleaned = clean_email(match.group(1))
        if cleaned:
            return cleaned
    for value in MAIL_RE.findall(html):
        cleaned = clean_email(value)
        if cleaned:
            return cleaned
    return ""


def extract_contacts(html: str, base: str, host: str) -> dict:
    """Pull public switchboard numbers and general mailboxes from a page.

    Priority: ``tel:`` links and numbers that sit next to a 电话/总机/客服 label,
    then any structurally valid landline. Mobile numbers are never kept — the
    directory does not publish an individual's contact details.

    Script / style blocks are stripped first: UUIDs, tracking ids and analytics
    payloads live there, and a UUID fragment such as ``...c1a7-4c2d-a027-0215656e237c``
    reads exactly like a landline number to a regex.
    """
    html = SCRIPT_BLOCK_RE.sub(" ", html)
    phone = ""
    for match in TEL_HREF_RE.finditer(html):
        phone = normalize_phone(match.group(1))
        if phone:
            break
    if not phone:
        for match in PHONE_CONTEXT_RE.finditer(html):
            phone = normalize_phone(match.group(1))
            if phone:
                break
    if not phone:
        for value in PHONE_CANDIDATE_RE.findall(html):
            phone = normalize_phone(value)
            if phone:
                break

    email = extract_public_email(html)
    if phone or email:
        return {"public_phone": phone, "public_email": email}

    # Try one linked contact page for the missing details.
    for match in re.finditer(r'href=["\']([^"\']*(?:' + "|".join(CONTACT_HINTS) + r')[^"\']*)["\']', html, re.I):
        link = match.group(1)
        target = urllib.parse.urljoin(base, link)
        if not same_site(urllib.parse.urlparse(target).netloc.lower().split(":")[0], host):
            continue
        try:
            _, _, page = http_get(target, timeout=12, max_bytes=200_000)
        except Exception:
            continue
        if not phone:
            found = TEL_HREF_RE.search(page) or PHONE_CONTEXT_RE.search(page)
            if found:
                phone = normalize_phone(found.group(1))
            if not phone:
                for value in PHONE_CANDIDATE_RE.findall(page):
                    phone = normalize_phone(value)
                    if phone:
                        break
        if not email:
            email = extract_public_email(page)
        if phone or email:
            break

    return {"public_phone": phone, "public_email": email}


# 候补官网清单（站方整理 / 第三方调研），都**未核实**，只是候选。
#
# 每个来源单独一个文件，而不是合并成一个大清单：来源决定候选的可信度，
# 合并之后就说不清某个域名是怎么来的了。两条来源最终都要过同一道验收门。
SEED_FILES = (
    "sites_seed.json",
    "research_sites.json",
    "seamless_sites.json",
    "cluster_reps_sites.json",
)


def seed_files() -> tuple[str, ...]:
    """内置候选清单 + ``/api/admin/lists/import`` 导入名单的候选官网层。"""
    path = ROOT / "data" / "curated" / "imported_lists.json"
    extra: list[str] = []
    if path.exists():
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            payload = {}
        extra = [e["sites_json"] for e in payload.get("lists", []) if e.get("sites_json")]
    return tuple(dict.fromkeys((*SEED_FILES, *extra)))


def load_seed() -> dict[str, str]:
    """Candidate official sites from every curated seed (站方整理 / 第三方调研,未核实)."""
    seeds: dict[str, str] = {}
    for filename in seed_files():
        path = ROOT / "data" / "curated" / filename
        if not path.exists():
            continue
        payload = json.loads(path.read_text(encoding="utf-8"))
        for row in payload.get("companies", []):
            seeds.setdefault(row["name"], row["website"])
    return seeds


def candidate_hosts(url: str) -> list[str]:
    """Host variants to try for a candidate URL, keeping ``www.`` and its bare form."""
    host = urllib.parse.urlparse(url).netloc.lower().split(":")[0]
    if not host:
        return []
    if host.startswith("www."):
        return [host, host[4:]]
    return [host, "www." + host]


def prepare_candidates(name: str, seeds: dict[str, str], use_search: bool = True) -> list[str]:
    """Candidate hosts for a company: curated seed first, then search discovery."""
    hosts: list[str] = []
    seeded = seeds.get(name)
    if seeded:
        hosts.extend(candidate_hosts(seeded))
    if use_search:
        for host in discover_candidates(name):
            if host not in hosts:
                hosts.append(host)
    return hosts


def load_companies() -> list[dict]:
    companies: dict[str, dict] = {}
    for filename, grade in (
        ("zhejiang_mingpin_2026.json", "official"),
        ("xiaojuren.json", "official"),
    ):
        path = SOURCE_DIR / filename
        if not path.exists():
            continue
        for row in json.loads(path.read_text(encoding="utf-8")):
            name = row["name"].strip()
            record = companies.setdefault(
                name,
                {
                    "name": name,
                    "list_names": [],
                    "region_raw": row.get("region_raw", ""),
                    "city": row.get("city", ""),
                    "district": row.get("district", ""),
                    "tracks": [],
                    "products": [],
                    "data_grade": grade,
                },
            )
            record["list_names"].append(row["list_name"])
            if row.get("track"):
                record["tracks"].append(row["track"])
            if row.get("product"):
                record["products"].append(row["product"])
    if not companies:
        fallback = ROOT / "data" / "curated" / "enriched.json"
        if fallback.exists():
            return json.loads(fallback.read_text(encoding="utf-8"))
    return list(companies.values())


def load_results() -> dict[str, dict]:
    if OUT.exists():
        try:
            rows = json.loads(OUT.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return {}
        return {row["name"]: row for row in rows}
    return {}


def merge_into_discovered(records: list[dict]) -> tuple[int, int]:
    """把一批「待核候选」并进 ``discovered.json``（新增 / 更新），返回 ``(新增, 更新)``。

    为什么必须走这一步：``discovered.json`` 是**联系方式合并里的自动发现层**，
    也是 `discover_sites.py` 的续跑状态文件。名单导入只写 ``_list.json`` 与
    ``_sites.json`` 时，新名单的官网不会被合并进联系方式层，也不会被验收门看见 ——
    表现在页面上就是「名单里有官网、页面却显示未核实」。

    合并规则：

    * **只增不替**：已经 ``verified`` 的记录原样保留（那是真去抓过的结论）；
    * 新增记录填 ``pending``，把候选域名放进 ``candidate_website``，**不写 website** ——
      候选域名没经过验收门，写进 website 就等于把清单的话当成了我们的核验结论。
    """
    # 目标显式写成 discovered.json：本模块的 OUT 指的是**早期候选层** enriched.json，
    # 复用它会把这批候选写进错误的层（页面标签会变成「站方候选 · 已抓取校验」）。
    path = ROOT / "data" / "curated" / "discovered.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    existing: dict[str, dict] = {}
    if path.exists():
        try:
            for row in json.loads(path.read_text(encoding="utf-8")):
                existing[row["name"]] = row
        except json.JSONDecodeError:
            existing = {}

    added = updated = 0
    for record in records:
        name = record.get("name")
        if not name:
            continue
        current = existing.get(name)
        if current is None:
            existing[name] = record
            added += 1
            continue
        # 已经抓过并核实的，不被候选覆盖；只补它缺失的「候选域名」线索
        if current.get("enrich_status") == "verified":
            continue
        merged = {**record, **{k: v for k, v in current.items() if v not in (None, "", [])}}
        if merged != current:
            existing[name] = merged
            updated += 1

    rows = sorted(existing.values(), key=lambda r: r["name"])
    path.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    return added, updated


def save_results(results: dict[str, dict]) -> None:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    rows = sorted(results.values(), key=lambda r: r["name"])
    OUT.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")


def enrich_one(company: dict, results: dict[str, dict], seeds: dict[str, str],
               no_search: bool = False) -> dict:
    name = company["name"]
    record = dict(company)
    record.setdefault("website", "")
    record.setdefault("public_phone", "")
    record.setdefault("public_email", "")
    record.setdefault("source_url", "")
    record.setdefault("verified_at", "")
    record.setdefault("enrich_status", "pending")

    hosts = prepare_candidates(name, seeds, use_search=not no_search)
    for host in hosts:
        meta = verify_site(host, name)
        if meta:
            record.update(
                website=meta["url"],
                public_phone=meta.get("public_phone", ""),
                public_email=meta.get("public_email", ""),
                source_url=meta["url"],
                verified_at=meta["checked_at"],
                enrich_status="verified",
                site_title=meta.get("title", ""),
            )
            break
    else:
        record["enrich_status"] = "no_site_found" if hosts else "no_candidate"
        record["verified_at"] = now_iso()

    results[name] = record
    return record


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=0, help="max companies to process")
    parser.add_argument("--only", action="append", default=[], help="company name substring")
    parser.add_argument("--delay", type=float, default=1.2, help="seconds between companies")
    parser.add_argument("--retry-empty", action="store_true", help="re-attempt companies without a site")
    parser.add_argument(
        "--refresh",
        action="store_true",
        help="re-fetch and re-extract contacts for companies already marked verified",
    )
    parser.add_argument("--sleep-min", type=float, default=0.0, help="jitter lower bound")
    parser.add_argument("--no-search", action="store_true", help="use the curated seed only")
    args = parser.parse_args()

    companies = load_companies()
    results = load_results()
    seeds = load_seed()

    todo = []
    for company in companies:
        if args.only and not any(key in company["name"] for key in args.only):
            continue
        existing = results.get(company["name"])
        if existing:
            status = existing.get("enrich_status")
            if status in ("verified", "enriched_curated") and not args.refresh:
                continue
            if status not in ("verified", "enriched_curated") and not args.retry_empty:
                continue
        todo.append(company)

    if args.limit:
        todo = todo[: args.limit]

    print(f"companies={len(companies)} results={len(results)} todo={len(todo)} seeded={len(seeds)}", flush=True)
    for index, company in enumerate(todo, 1):
        try:
            record = enrich_one(company, results, seeds, no_search=args.no_search)
        except Exception as exc:  # keep the batch alive; individual sites are flaky
            record = dict(company)
            record.update(enrich_status="error", error=type(exc).__name__, verified_at=now_iso())
            results[company["name"]] = record
        print(
            f"[{index}/{len(todo)}] {record['enrich_status']:14s} {record['name'][:34]:34s} "
            f"{record.get('website', '')}",
            flush=True,
        )
        if index % 10 == 0:
            save_results(results)
        time.sleep(max(args.delay, 0) + random.uniform(0, args.sleep_min))
    save_results(results)
    verified = sum(1 for r in results.values() if r.get("enrich_status") == "verified")
    print(f"done: {len(results)} records, {verified} verified sites -> {OUT}")


if __name__ == "__main__":
    main()