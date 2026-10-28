"""预渲染「值得排名」的页面，让爬虫在**不执行 JS** 时也能读到正文。

为什么需要它（审计 2026-10-28 的核心结论）：全站是客户端渲染的 SPA，nginx 对
任何路径都回同一份 3KB 的 ``index.html``，正文由浏览器跑完 JS 再向后端取数。
Google 会排队渲染 JS，但百度、Bing 中文、微博/微信内搜基本不执行；
GPTBot / ClaudeBot / PerplexityBot / Bytespider 更是不执行 ——
对 AI 引擎来说，4,000 多家企业与 200 多个集群档案一个字都进不了语料。

做法：用 Playwright 打开构建产物（``vite preview``），等数据落位后把
``page.content()`` 写回 ``dist/<path>/index.html``。这样做的关键好处是
**head 与正文由同一套 Vue 代码产生**（``src/seo.js`` 的 applySeo），
不存在「构建时注入的 meta」与「运行时改写的 meta」两份实现漂移的问题。

不预渲染什么、为什么：

* 薄页集群（``is_stub``，挂载企业 < 10 家）—— 提交一堆「集群名 + 一句话」
  的页面，只会让搜索引擎判定整站质量偏低；企业数补到 10 家会自动进来；
* 企业详情页（4,000+）—— 数量大、正文短，先把集群页与静态页做扎实；
* ``/research`` 与 ``/research/<slug>`` —— 要渲染（它们现在也是站点路由，正文嵌在页面里）；
* 登录 / 收藏 / 后台 —— ``noindex``，预渲染它们没有意义。

用法（先构建，再预渲染）::

    cd frontend && npm run build && cd ..
    .venv/bin/python tools/prerender.py

    # 已经有 preview 在跑着（省一次启动；注意它必须服务的是**当前** dist）
    .venv/bin/python tools/prerender.py --no-serve --base http://127.0.0.1:4173

    # 先看看会渲染哪些 URL，不落盘
    .venv/bin/python tools/prerender.py --dry-run

前置条件：后端在 5172 上跑着（预渲染要取真实数据），playwright 已装在 ``.venv``。
"""

from __future__ import annotations

import argparse
import json
import os
import signal
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import quote

ROOT = Path(__file__).resolve().parents[1]
FRONTEND = ROOT / "frontend"
DIST = FRONTEND / "dist"

# 静态页：与 backend/app/routers/sitemap.py 的 STATIC_PATHS 保持一致。
# /research 与每篇文章由 research_paths() 从接口拉，不写死在这里（导入一篇就多一页）。
STATIC_PATHS = ["/", "/map", "/clusters", "/enterprises", "/about", "/company", "/service"]

# 单页最多等多久。1 分钟是给「灌库后第一屏」留的余量，正常 2–4 秒。
PAGE_TIMEOUT_MS = 60_000


def http_ok(url: str, timeout: float = 2.0) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            return response.status < 500
    except (urllib.error.URLError, OSError, ValueError):
        return False


def cluster_paths(api_base: str, include_stubs: bool) -> tuple[list[str], int]:
    """从后端拉集群清单。

    刻意**不**手写 URL：集群增删是常态，写死的清单一定会过期，而过期的清单
    会让「sitemap 里的 URL」与「磁盘上的预渲染产物」对不上 —— 那正是软 404 的来源。
    """
    paths: list[str] = []
    skipped = 0
    page = 1
    pages = 1
    while page <= pages:
        url = f"{api_base}/clusters?page_size=100&page={page}"
        with urllib.request.urlopen(url, timeout=30) as response:
            payload = json.loads(response.read().decode())
        pages = payload["meta"]["pages"]
        for item in payload["items"]:
            if item.get("is_stub") and not include_stubs:
                skipped += 1
                continue
            paths.append(f"/clusters/{quote(item['slug'], safe='')}")
        page += 1
    return paths, skipped


def research_paths(api_base: str) -> list[str]:
    """行业研究：列表页一页 + 每篇文章一页。

    这两类页面现在是**站点自己的路由**（正文由 ``/api/research/<slug>`` 取回后嵌进页面），
    所以必须预渲染 —— 否则「行业研究」就退回「爬虫拿到空白页」的老问题。
    文章清单同样从接口拉：后台导入一篇就该进预渲染，不需要改脚本。
    """
    paths = ["/research"]
    try:
        with urllib.request.urlopen(f"{api_base}/research", timeout=30) as response:
            payload = json.loads(response.read().decode())
    except (urllib.error.URLError, ValueError) as exc:
        print(f"  ! 取行业研究清单失败（{exc}），本次只渲染列表页")
        return paths
    for item in payload.get("items") or []:
        paths.append(f"/research/{quote(item['slug'], safe='')}")
    return paths


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def start_preview(port: int) -> subprocess.Popen:
    """起一个**新的** ``vite preview``。

    刻意不复用「4173 上已经有人在响应」：那个进程可能是上一次运行残留的，
    而它手里缓存的还是**旧 dist**（``npm run build`` 会换掉所有带哈希的文件名），
    于是新构建的页面在它那儿全是 404 —— 表现是随机某一页等不到 ``h1``，
    排查时会以为是页面代码坏了。端口每次现取，且独立进程组，退出时整组收掉。
    """
    print(f"  启动 vite preview :{port} …")
    # 直接调本地 bin，不走 npx：npx 在机器负载高时要多花十几秒做解析，
    # 而这里只差一个路径。
    vite_bin = FRONTEND / "node_modules" / ".bin" / "vite"
    command = [str(vite_bin)] if vite_bin.exists() else ["npx", "vite"]
    process = subprocess.Popen(
        [*command, "preview", "--port", str(port), "--strictPort"],
        cwd=FRONTEND,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.STDOUT,
        start_new_session=True,  # npx 会派生子进程，只有整组收掉才不会留下占端口的孤儿
    )
    base = f"http://127.0.0.1:{port}"
    deadline = time.time() + 180
    while time.time() < deadline:
        if http_ok(base):
            return process
        if process.poll() is not None:
            raise SystemExit("vite preview 启动失败（端口被占或构建产物缺失）")
        time.sleep(0.5)
    stop_preview(process)
    raise SystemExit("vite preview 180 秒内没有就绪（先确认 frontend 能 npm run preview）")


def stop_preview(process: subprocess.Popen) -> None:
    if process.poll() is not None:
        return
    try:
        os.killpg(os.getpgid(process.pid), signal.SIGTERM)
    except (ProcessLookupError, PermissionError):  # pragma: no cover - 已自行退出
        return
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:  # pragma: no cover - 极端情况
        os.killpg(os.getpgid(process.pid), signal.SIGKILL)


def write_page(dist: Path, url_path: str, html: str) -> Path:
    # 目录名必须用**解码后**的路径：集群 slug 带中文（``415X-人工智能``），
    # URL 里是百分号编码的，而 nginx 是把请求 URI 解码后去文件系统找目录的。
    # 落成 ``dist/clusters/415X-%E4%BA%BA.../`` 的话，线上永远命不中，白渲染。
    from urllib.parse import unquote

    cleaned = unquote(url_path).strip("/")
    target_dir = dist / cleaned if cleaned else dist
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / "index.html"
    target.write_text(html, encoding="utf-8")
    return target


def not_found_page(site_title: str) -> str:
    """``dist/404.html``：nginx 的 ``try_files … =404`` 落在这里。

    刻意**不放** SPA 的 index.html：软 404（200 + 空白页）会让搜索引擎把
    不存在的路径也当成有效页面收录，是审计里明确点名的问题。
    """
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>页面不存在 · {site_title}</title>
<meta name="robots" content="noindex, follow">
<style>
  body{{margin:0;background:#f8f7f5;color:#1a1d21;font:16px/1.75 -apple-system,"PingFang SC","Microsoft YaHei",sans-serif}}
  .wrap{{max-width:640px;margin:0 auto;padding:96px 24px}}
  h1{{font-size:24px;margin:0 0 10px}} p{{color:#4b5563;margin:0 0 22px}}
  a{{color:#1d4ed8}}
</style>
</head>
<body>
<div class="wrap">
  <h1>没有这个页面</h1>
  <p>链接可能已经失效，或者地址打错了一个字。</p>
  <p><a href="/">返回首页</a> · <a href="/clusters">产业集群</a> · <a href="/enterprises">企业名录</a></p>
</div>
</body>
</html>"""


def main() -> int:
    parser = argparse.ArgumentParser(description="预渲染需要被搜索抓到的页面")
    parser.add_argument("--base", default="http://127.0.0.1:4173", help="配合 --no-serve：已在运行的 preview 地址")
    parser.add_argument("--api", default="http://127.0.0.1:5172", help="后端地址（取集群清单）")
    parser.add_argument("--dist", default=str(DIST), help="构建产物目录")
    parser.add_argument("--port", type=int, default=0, help="vite preview 端口（默认现取一个空闲端口）")
    parser.add_argument("--no-serve", action="store_true", help="假设 preview 已在运行")
    parser.add_argument("--include-stubs", action="store_true", help="连薄页集群一起渲染（默认跳过）")
    parser.add_argument("--dry-run", action="store_true", help="只打印 URL 清单，不落盘")
    parser.add_argument(
        "--only",
        action="append",
        default=[],
        metavar="PATH",
        help="只重渲染指定路径（可重复），例：--only /clusters --only /enterprises",
    )
    args = parser.parse_args()

    dist = Path(args.dist).resolve()
    if not (dist / "index.html").exists():
        raise SystemExit(f"{dist}/index.html 不存在，先跑 `cd frontend && npm run build`")
    if not http_ok(f"{args.api}/api/health"):
        raise SystemExit(
            f"后端 {args.api} 不可达。预渲染要取真实数据 —— 先用 ./zanzhu.sh start 把它起来"
        )

    print("① 收集 URL")
    urls = list(STATIC_PATHS)
    clusters, skipped = cluster_paths(f"{args.api}/api", args.include_stubs)
    urls.extend(clusters)
    articles = research_paths(f"{args.api}/api")
    urls.extend(articles)
    print(
        f"  静态页 {len(STATIC_PATHS)} · 集群 {len(clusters)} · 行业研究 {len(articles)}"
        f" · 跳过薄页 {skipped} · 合计 {len(urls)}"
    )

    if args.only:
        missing = [path for path in args.only if path not in urls]
        if missing:
            raise SystemExit(f"--only 里有不在清单中的路径：{missing}")
        urls = [url for url in urls if url in set(args.only)]
        print(f"  只渲染指定路径：{len(urls)} 页")

    if args.dry_run:
        for url in urls:
            print("  " + url)
        return 0

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        raise SystemExit("缺 playwright：.venv/bin/pip install playwright && .venv/bin/python -m playwright install chromium")

    if args.no_serve:
        if not http_ok(args.base):
            raise SystemExit(f"--no-serve 但 {args.base} 上没人响应")
        base = args.base
        process = None
    else:
        port = args.port or free_port()
        process = start_preview(port)
        base = f"http://127.0.0.1:{port}"

    written = 0
    failures: list[str] = []
    started = time.time()

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_page(viewport={"width": 1280, "height": 900})
            try:
                for index, url in enumerate(urls, start=1):
                    html = None
                    last_error = ""
                    # 每页最多试两次：125 页里偶发的 networkidle 抖动不该让整批带上
                    # 「这一页没渲染」的结论（而真错误两次都会失败，照样报出来）。
                    for attempt in (1, 2):
                        try:
                            page.goto(base + url, wait_until="networkidle", timeout=PAGE_TIMEOUT_MS)
                            # h1 只证明「页面挂上了」，列表页的 h1 是同步渲染的，
                            # 数据要等接口回来 —— 所以还要再等一次网络安静，
                            # 并等到加载态消失（.loading 是各页统一的加载占位）。
                            page.wait_for_selector("h1", timeout=PAGE_TIMEOUT_MS)
                            page.wait_for_load_state("networkidle", timeout=PAGE_TIMEOUT_MS)
                            page.wait_for_function(
                                "() => !document.querySelector('.loading')",
                                timeout=PAGE_TIMEOUT_MS,
                            )
                            page.wait_for_timeout(120)  # 让 applySeo 的 head 写入落定
                            html = page.content()
                            break
                        except Exception as exc:  # noqa: BLE001 - 记录后重试一次
                            last_error = f"{type(exc).__name__}: {exc}".splitlines()[0]
                            if attempt == 2:
                                failures.append(f"{url}：{last_error}")
                    if html is None:
                        continue
                    if "加载中" in html:
                        # 不中断整批：一页失败不该让其余一百多页都不落盘，
                        # 但必须让人看见是哪一页，否则线上就是「这一页没内容」。
                        failures.append(f"{url}（仍在加载态）")
                    write_page(dist, url, html)
                    written += 1
                    if index % 20 == 0:
                        print(f"  … {index}/{len(urls)}")
            finally:
                browser.close()
    finally:
        if process is not None:
            stop_preview(process)

    (dist / "404.html").write_text(not_found_page("杭州波动粒子"), encoding="utf-8")
    seconds = time.time() - started
    print(f"② 完成：写入 {written}/{len(urls)} 页 + 404.html，用时 {seconds:.0f} 秒 → {dist}")
    if failures:
        print("   以下页面没渲染成功：")
        for failure in failures:
            print("   - " + failure)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
