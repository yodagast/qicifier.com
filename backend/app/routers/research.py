"""``/api/research`` — 行业研究文档：后台导入 HTML，前端嵌进站点页面。

存储与导入是文件（``data/research/<slug>.html`` + ``index.json``），与名单导入同一条
原则：**产物文件才是数据源**。删掉一个 HTML 就下线一篇，不需要建表、不需要灌库
（这一点与名单 CSV 相反：名单导入后必须 re-seed，研究文档导入即生效）。

为什么这里**不**直出整份 HTML：

* 行业研究是站点的内容栏目，要长在页头 / 页脚 / 导航之间，而不是把整站替换掉 ——
  整份文档塞进页面会带上它自己的 ``<html>`` / ``<head>`` / ``<title>``；
* 文档自带 ``<style>``（``body{}`` / ``*{}`` / ``:root``），直接注入会污染全站样式，
  所以样式单独返回，由前端统一加作用域前缀（见 ``frontend/src/researchDoc.js``）；
* 可索引性由**预渲染**保证（``tools/prerender.py`` 渲染 ``/research`` 与每篇文章），
  和集群 / 企业页走同一条路，而不是两套机制。

正文里的 ``<script>`` 在服务端就被剥掉；前端还会再做一次 DOM 清洗（``on*`` 属性、
``javascript:`` 链接、``iframe`` 等）。两层都留着，是因为这份内容最终进的是**同源**
页面，而登录令牌就在 localStorage 里。
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile

from ..config import settings
from ..services.admin_auth import require_admin

router = APIRouter(tags=["research"])
admin_router = APIRouter(prefix="/api/admin/research", tags=["admin"])

# 2MB 足够放一份带内联样式的长文（现有方案文档 40KB 量级），
# 再大就不是「一篇研究」而是「一个站点」了。
MAX_BYTES = 2 * 1024 * 1024

# slug 可以带中文（集群 slug 已经这么做了，形如 ``415X-人工智能``），
# 所以不能只允许 ASCII；但必须挡住路径分隔符与 ``..``，否则能写出 data/research/ 之外。
SLUG_RE = re.compile(r"^[0-9A-Za-z\u4e00-\u9fa5][0-9A-Za-z\u4e00-\u9fa5._-]*$")

TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.S | re.I)
PARA_RE = re.compile(r"<p[^>]*>(.*?)</p>", re.S | re.I)
TAG_RE = re.compile(r"<[^>]+>")

STYLE_RE = re.compile(r"<style\b[^>]*>(.*?)</style>", re.S | re.I)
SCRIPT_RE = re.compile(r"<script\b[^>]*>.*?</script>", re.S | re.I)
BODY_RE = re.compile(r"<body\b[^>]*>(.*?)</body>", re.S | re.I)
HEAD_RE = re.compile(r"<head\b[^>]*>.*?</head>", re.S | re.I)


def research_dir() -> Path:
    return settings.data_dir / "research"


def index_file() -> Path:
    return research_dir() / "index.json"


def _load() -> list[dict]:
    path = index_file()
    if not path.exists():
        return []
    return json.loads(path.read_text(encoding="utf-8"))


def _save(entries: list[dict]) -> None:
    research_dir().mkdir(parents=True, exist_ok=True)
    index_file().write_text(json.dumps(entries, ensure_ascii=False, indent=2), encoding="utf-8")


def published_articles() -> list[dict]:
    """按发布时间倒序。列表页、sitemap、预渲染清单共用这一份排序。"""
    return sorted(_load(), key=lambda article: article.get("published_at", ""), reverse=True)


def _plain(fragment: str) -> str:
    """把一段 HTML 压成单行纯文本（只用于抽标题/摘要，不用于展示正文）。"""
    return " ".join(TAG_RE.sub(" ", fragment or "").split())


def split_document(html: str) -> tuple[str, list[str]]:
    """拆出「正文」与「文档自带的样式」。

    * 有 ``<body>`` 就只取 body 内部；没有（片段式文档）则去掉 ``<head>`` 与 ``<style>``；
    * 样式单独收集，交给前端加作用域前缀 —— 见模块文档；
    * ``<script>`` 一律剥掉：留一个「只要不显示就安全」的脚本在这里，
      等于把同源 XSS 的开关交给上传者。
    """
    styles = [match.group(1).strip() for match in STYLE_RE.finditer(html) if match.group(1).strip()]

    body_match = BODY_RE.search(html)
    if body_match:
        body = body_match.group(1)
    else:
        body = STYLE_RE.sub("", HEAD_RE.sub("", html))

    body = SCRIPT_RE.sub("", body)
    return body.strip(), styles


@router.get("/api/research")
def list_articles():
    """列表只回元数据（标题 / 摘要 / 日期 / slug），正文按需再取。"""
    return {"items": published_articles()}


@router.get("/api/research/{slug}")
def get_article(slug: str):
    """单篇：元数据 + 正文片段 + 文档自带样式。

    ``body`` 已经不是完整文档，前端可安全嵌进 ``<div class="research-doc">``；
    样式由前端加作用域前缀后再注入（不这么做，文档里的 ``body{}`` 会改掉全站排版）。
    """
    entry = next((article for article in _load() if article["slug"] == slug), None)
    if entry is None:
        raise HTTPException(status_code=404, detail="研究文档不存在")

    path = research_dir() / f"{slug}.html"
    if not path.exists():
        # 清单与文件不同步（多半是手工删了文件）：当成不存在，而不是 500
        raise HTTPException(status_code=404, detail="研究文档不存在")

    body, styles = split_document(path.read_text(encoding="utf-8"))
    return {"article": entry, "body": body, "styles": styles}


def _decode(raw: bytes) -> str:
    for encoding in ("utf-8-sig", "gb18030"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise HTTPException(status_code=422, detail="无法识别文档编码，请存成 UTF-8 后再导入")


@admin_router.get("")
def list_for_admin(_: None = Depends(require_admin)):
    return {"items": published_articles(), "hint": "同一个 slug 再导入 = 覆盖该篇"}


@admin_router.post("/import")
async def import_doc(
    file: UploadFile = File(...),
    slug: str = Form(""),
    title: str = Form(""),
    summary: str = Form(""),
    published_at: str = Form(""),
    _: None = Depends(require_admin),
):
    """导入 / 覆盖一篇研究文档。

    ``slug`` 留空则取文件名（不含扩展名）—— 现有文档的文件名就是中文标题，
    正好可以直接当 slug（与集群 slug 同风格）。标题与摘要留空时从文档里抽。
    """
    raw = await file.read()
    if len(raw) > MAX_BYTES:
        raise HTTPException(status_code=413, detail=f"文档超过 {MAX_BYTES // 1024 // 1024}MB")
    if not raw.strip():
        raise HTTPException(status_code=422, detail="文件是空的")

    html = _decode(raw)
    lowered = html.lower()
    if "<html" not in lowered or "</html>" not in lowered:
        raise HTTPException(
            status_code=422,
            detail="不是完整的 HTML 文档（缺 <html> / </html>）。请上传整份文档，不要只贴正文片段",
        )

    slug = (slug or Path(file.filename or "").stem).strip()
    if not SLUG_RE.match(slug) or ".." in slug:
        raise HTTPException(
            status_code=422,
            detail=f"slug 不合法：{slug!r}。只允许中英文、数字、点、下划线、连字符",
        )

    if not title:
        match = TITLE_RE.search(html)
        title = _plain(match.group(1)) if match else ""
    title = title.strip()
    if not title:
        raise HTTPException(status_code=422, detail="没从文档里读到 <title>，请显式填写标题")

    if not summary:
        match = PARA_RE.search(html)
        summary = _plain(match.group(1))[:160] if match else ""
    summary = summary.strip()

    today = datetime.now().strftime("%Y-%m-%d")
    entry = {
        "slug": slug,
        "title": title,
        "summary": summary,
        "published_at": (published_at or today).strip(),
        "updated_at": today,
        "source_file": file.filename or "",
    }
    entries = [article for article in _load() if article["slug"] != slug] + [entry]
    _save(entries)

    research_dir().mkdir(parents=True, exist_ok=True)
    (research_dir() / f"{slug}.html").write_text(html, encoding="utf-8")

    warnings = []
    if "<script" in lowered:
        warnings.append("文档含 <script>：嵌入页面时会剥掉，不会执行")
    if "http://" in lowered:
        warnings.append("文档引用了 http:// 资源：https 页面下会被浏览器拦截，建议改成 https")

    return {"ok": True, "article": entry, "url": f"/research/{slug}", "warnings": warnings}


@admin_router.delete("/{slug}")
def delete_doc(slug: str, _: None = Depends(require_admin)):
    entries = _load()
    if not any(article["slug"] == slug for article in entries):
        raise HTTPException(status_code=404, detail="研究文档不存在")
    _save([article for article in entries if article["slug"] != slug])
    (research_dir() / f"{slug}.html").unlink(missing_ok=True)
    return {"ok": True, "removed": slug}
