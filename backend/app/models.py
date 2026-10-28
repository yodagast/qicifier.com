"""SQLAlchemy models mirroring the four-layer data model from the research plan.

Layers (kept deliberately separate — see docs/浙江产业集群收录体系搭建方案.html §01–§04):

1. ``Region``          — 省 / 市 / 县(市、区) / 有产业的镇街 四级地理层
2. ``Cluster``         — 集群，一等公民，与行政区解耦（cluster_region 关系表带角色）
3. ``Enterprise``      — 企业，逐条标 data_grade 与来源
4. ``EnterpriseCluster`` — 企业 × 集群 多对多关系（可标主集群）
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class Region(Base, TimestampMixin):
    """四级地理层。``level`` ∈ province / city / district / town。"""

    __tablename__ = "region"
    __table_args__ = (UniqueConstraint("code", name="uq_region_code"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    code: Mapped[str] = mapped_column(String(32), index=True, unique=True)
    name: Mapped[str] = mapped_column(String(64), index=True)
    level: Mapped[str] = mapped_column(String(16), index=True)
    parent_code: Mapped[str | None] = mapped_column(String(32), ForeignKey("region.code"), nullable=True)
    data_grade: Mapped[str] = mapped_column(String(16), default="official")
    has_industry: Mapped[bool] = mapped_column(Boolean, default=False)
    sources: Mapped[list[dict]] = mapped_column(JSONB, default=list)


class Cluster(Base, TimestampMixin):
    """产业集群。``data_grade`` 区分 官方认定 / 政府公开 / 媒体·协会 / 站方整理。"""

    __tablename__ = "cluster"
    __table_args__ = (UniqueConstraint("slug", name="uq_cluster_slug"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    slug: Mapped[str] = mapped_column(String(96), index=True, unique=True)
    name: Mapped[str] = mapped_column(String(120), index=True)
    aliases: Mapped[list[str]] = mapped_column(ARRAY(String), default=list)
    industry_l1: Mapped[str] = mapped_column(String(64), index=True, default="")
    industry_l2: Mapped[str] = mapped_column(String(64), default="")
    sector: Mapped[str] = mapped_column(String(64), default="")
    tier: Mapped[str] = mapped_column(String(2), default="C")
    scale_tier: Mapped[str] = mapped_column(String(24), default="")
    core_area: Mapped[str] = mapped_column(String(120), default="")
    honors: Mapped[list[dict]] = mapped_column(JSONB, default=list)
    metrics: Mapped[list[dict]] = mapped_column(JSONB, default=list)
    summary: Mapped[str] = mapped_column(Text, default="")
    # 简介**自己**的核实程度：集群可以是官方认定的，但简介可能是第三方调研或站方写的。
    # 不区分就会让一段调研方的话读起来像官方表述。
    summary_grade: Mapped[str] = mapped_column(String(16), default="")
    sources: Mapped[list[dict]] = mapped_column(JSONB, default=list)
    data_grade: Mapped[str] = mapped_column(String(16), default="site_curated", index=True)
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    regions: Mapped[list["ClusterRegion"]] = relationship(
        back_populates="cluster", cascade="all, delete-orphan"
    )
    enterprise_links: Mapped[list["EnterpriseCluster"]] = relationship(
        back_populates="cluster", cascade="all, delete-orphan"
    )


class ClusterRegion(Base):
    """集群 × 行政区多对多。``role`` ∈ 核心区 / 协同区 / 辐射区 / 唯一产地 / 市级归口。"""

    __tablename__ = "cluster_region"
    __table_args__ = (UniqueConstraint("cluster_id", "region_id", name="uq_cluster_region"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    cluster_id: Mapped[int] = mapped_column(ForeignKey("cluster.id", ondelete="CASCADE"), index=True)
    region_id: Mapped[int] = mapped_column(ForeignKey("region.id", ondelete="CASCADE"), index=True)
    role: Mapped[str] = mapped_column(String(16), default="核心区")
    is_primary: Mapped[bool] = mapped_column(Boolean, default=False)

    cluster: Mapped[Cluster] = relationship(back_populates="regions")
    region: Mapped[Region] = relationship()


class Enterprise(Base, TimestampMixin):
    """企业记录。排序只看 data_grade 与资料完整度，收录不收费、不卖排名。"""

    __tablename__ = "enterprise"
    __table_args__ = (UniqueConstraint("name", name="uq_enterprise_name"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(160), index=True)
    name_en: Mapped[str] = mapped_column(String(200), default="")
    city: Mapped[str] = mapped_column(String(32), index=True, default="")
    district: Mapped[str] = mapped_column(String(32), index=True, default="")
    town: Mapped[str] = mapped_column(String(32), index=True, default="")
    # 地区来源：official_list = 名单原文「所属地区」；name_prefix = 据企业名称前缀推断（页面需标注）；
    # research_list = 第三方调研清单给的地区；out_of_province = 省外（不参与浙江统计）；
    # benchmark = 清单标为「对标龙头」但企业自己在浙江（不参与该产区统计，仍属浙江）
    region_source: Mapped[str] = mapped_column(String(16), default="official_list")
    region_id: Mapped[int | None] = mapped_column(
        ForeignKey("region.id", ondelete="SET NULL"), nullable=True, index=True
    )

    summary: Mapped[str] = mapped_column(Text, default="")
    products: Mapped[list[str]] = mapped_column(ARRAY(String), default=list)
    honors: Mapped[list[dict]] = mapped_column(JSONB, default=list)
    founded_year: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # 注册资本按**原文**存字符串（「100万元」「2,880万人民币」写法不一，折算成数字
    # 会引入我们没能力核实的汇率/口径判断）
    registered_capital: Mapped[str] = mapped_column(String(64), default="")
    # 第三方调研清单的**逐行核实档位**（A 官网公示 / B 工商可核 / C 线索未核）。
    # 名单级 data_grade 说明「谁给的名单」，这个字段说明「这一行核到什么程度」——
    # 同一份清单里这两件事的差距可能很大，必须分开存。
    verify_tier: Mapped[str] = mapped_column(String(24), default="")
    # 产区业态（代工型 / 品牌型）。调研方案指出两类产区的字段需求不同，
    # 混在一起会让「起订量/能否 OEM」和「渠道/加盟」互相打架。
    business_model: Mapped[str] = mapped_column(String(48), default="")
    # 工商注册地。企业实际可能有注册地/办公地/厂址多处，只存一个地址字符串
    # 无论存哪个都会被质疑，所以注册地在与展示地址不同时单独存。
    address_registered: Mapped[str] = mapped_column(String(255), default="")

    website: Mapped[str] = mapped_column(String(255), default="")
    # 这个链接**是什么**：企业官网 / 集团官网 / 母站企业官网 / 科研机构官网。
    # 「集团官网」也收进 website，但必须标出来 —— 采购商需要知道点进去的是集团站
    # 而不是工厂自己的站（调研文档专门警告过这一点）；第三方商铺页则一律不进 website。
    website_kind: Mapped[str] = mapped_column(String(24), default="")
    # 号码性质（官网总机 / 第三方总机 / 待核）。镇街级清单里「有官网」与
    # 「打得通电话」是两件事，必须逐条交代号码是怎么来的。
    phone_nature: Mapped[str] = mapped_column(String(48), default="")
    address_type: Mapped[str] = mapped_column(String(24), default="")
    public_phone: Mapped[str] = mapped_column(String(64), default="")
    public_email: Mapped[str] = mapped_column(String(120), default="")
    address: Mapped[str] = mapped_column(String(255), default="")
    # 联系方式来源标签（website/phone/email/address → 「站方核验 · 已比对官网」等）
    contact_sources: Mapped[dict] = mapped_column(JSONB, default=dict)
    contact_status: Mapped[str] = mapped_column(String(12), default="empty")

    list_names: Mapped[list[str]] = mapped_column(ARRAY(String), default=list)
    source_url: Mapped[str] = mapped_column(String(255), default="")
    data_grade: Mapped[str] = mapped_column(String(16), default="official", index=True)
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    enrich_status: Mapped[str] = mapped_column(String(24), default="")
    manual_note: Mapped[str] = mapped_column(Text, default="")

    cluster_links: Mapped[list["EnterpriseCluster"]] = relationship(
        back_populates="enterprise", cascade="all, delete-orphan"
    )


class EnterpriseCluster(Base):
    """企业 × 集群。允许一企多集群，但必须有一个 ``is_primary``。

    ``attribution`` 记录归属判定依据（调研方案 §04）：
    * ``product``  主营产品与 415X 赛道一致，可判定
    * ``district_fallback``  无法判定时归入注册地所在县的主导集群，页面标「归属待确认」
    """

    __tablename__ = "enterprise_cluster"
    __table_args__ = (UniqueConstraint("enterprise_id", "cluster_id", name="uq_enterprise_cluster"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    enterprise_id: Mapped[int] = mapped_column(
        ForeignKey("enterprise.id", ondelete="CASCADE"), index=True
    )
    cluster_id: Mapped[int] = mapped_column(ForeignKey("cluster.id", ondelete="CASCADE"), index=True)
    is_primary: Mapped[bool] = mapped_column(Boolean, default=True)
    attribution: Mapped[str] = mapped_column(String(24), default="product")
    needs_confirmation: Mapped[bool] = mapped_column(Boolean, default=False)

    enterprise: Mapped[Enterprise] = relationship(back_populates="cluster_links")
    cluster: Mapped[Cluster] = relationship(back_populates="enterprise_links")
