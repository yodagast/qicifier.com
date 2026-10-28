"""Pydantic response schemas.

``data_grade`` labels are surfaced on every entity so the UI can render the
核实程度 tag required by the research plan (§05):

* ``official``     官方认定 · 附来源链接与核验日期
* ``gov_open``     政府公开信息
* ``media``        媒体 · 协会来源
* ``site_curated`` 站方整理（未逐条核实，仅作检索线索）
* ``self_reported`` 企业自报 · 本站未核验
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict


GRADE_LABELS = {
    "official": "官方认定",
    "gov_open": "政府公开信息",
    "media": "媒体 · 协会来源",
    "site_curated": "站方整理",
    "self_reported": "企业自报 · 本站未核验",
}

# 集群建档但挂 0 家企业 = 空壳集群（调研方案 §06 判据③、§09 自检清单）
STUB_CLUSTER_THRESHOLD = 10


class RegionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    code: str
    name: str
    level: str
    parent_code: str | None = None
    has_industry: bool = False
    # 省级新区 / 开发区（Z 前缀自定义码）不是国标县级行政区，单独标记，
    # 避免把「90 个县级行政区」被这类认定主体混进同一条计数里。
    is_zone: bool = False
    enterprise_count: int = 0
    cluster_count: int = 0
    children: list["RegionOut"] = []


class TrackOut(BaseModel):
    slug: str
    name: str
    sector: str = ""
    cluster_count: int = 0
    enterprise_count: int = 0


class ClusterSummary(BaseModel):
    slug: str
    name: str
    industry_l1: str = ""
    industry_l2: str = ""
    # 「产业」列显示用的拼接串（``l1 · l2``，缺的略过）。由后端算好后给出，
    # 前端不再自己拼一遍 —— 列表排序用的也是这个串，两处各拼一次就会不一致。
    industry_label: str = ""
    tier: str = "C"
    scale_tier: str = ""
    core_area: str = ""
    city: str = ""
    district: str = ""
    town: str = ""
    data_grade: str = "site_curated"
    data_grade_label: str = ""
    enterprise_count: int = 0
    is_stub: bool = False


class ClusterDetail(ClusterSummary):
    aliases: list[str] = []
    sector: str = ""
    honors: list[dict] = []
    metrics: list[dict] = []
    summary: str = ""
    # 简介**自己**的核实程度：集群是官方认定的，不等于这段简介也是。
    summary_grade: str = ""
    summary_grade_label: str = ""
    sources: list[dict] = []
    verified_at: datetime | None = None
    regions: list[dict] = []
    enterprises: list["EnterpriseSummary"] = []


class EnterpriseSummary(BaseModel):
    id: int
    name: str
    city: str = ""
    district: str = ""
    town: str = ""
    region_source: str = "official_list"
    summary: str = ""
    products: list[str] = []
    founded_year: int | None = None
    registered_capital: str = ""
    verify_tier: str = ""
    business_model: str = ""
    website: str = ""
    website_kind: str = ""
    public_phone: str = ""
    phone_nature: str = ""
    address_type: str = ""
    public_email: str = ""
    address: str = ""
    address_registered: str = ""
    contact_status: str = "empty"
    contact_sources: dict = {}
    data_grade: str = "official"
    data_grade_label: str = ""
    list_names: list[str] = []
    cluster_names: list[str] = []
    attribution_note: str = ""


class EnterpriseDetail(EnterpriseSummary):
    name_en: str = ""
    honors: list[dict] = []
    source_url: str = ""
    verified_at: datetime | None = None
    enrich_status: str = ""
    manual_note: str = ""
    clusters: list[dict] = []
    completeness: int = 0


class PageMeta(BaseModel):
    total: int
    page: int
    page_size: int
    pages: int


class EnterprisePage(BaseModel):
    items: list[EnterpriseSummary]
    meta: PageMeta


class ClusterPage(BaseModel):
    items: list[ClusterSummary]
    meta: PageMeta


class StatsOut(BaseModel):
    enterprise_total: int
    enterprise_out_of_province: int = 0
    enterprise_official: int
    enterprise_with_website: int
    enterprise_with_phone: int
    cluster_total: int
    cluster_official: int
    region_total: int
    track_total: int
    list_breakdown: list[dict] = []
    grade_breakdown: list[dict] = []