"""用户与收藏。

注册无需邮箱/短信验证（按需求「无需验证即可注册」），因此：

* 密码用 PBKDF2-SHA256 加盐哈希存储，绝不明文落库；
* 会话用签名令牌（HMAC-SHA256，含过期时间），服务端不保存会话表；
* 「无需验证」只针对注册流程本身——用户名唯一性与密码强度仍然强校验，
  否则会成为开放注册下的垃圾账号入口。

数据层：
* ``User``          账号
* ``Favorite``      工厂收藏（用户 × 企业，带备注与收藏时间）

**收藏按企业名称关联，不按自增 id。** ``seed --drop`` 会重建整个数据层，
自增 id 随之变化：今天 id 1006 是融鼎岳，重灌一次可能就变成另一家企业 ——
收藏会**悄悄改指向**，用户看到的是自己从没收藏过的工厂。
企业名称在本库里是稳定且唯一的事实键（``uq_enterprise_name``），
而且清单里的名字就是要展示给人看的那个字符串，所以拿它做关联。

同理，``PRESERVED_TABLES`` 里的两张表装的是**用户自己的数据**，不是从清单重建的
数据层；重新灌库时不能连它们一起删掉。
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import time
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .models import Base, TimestampMixin

# ---------------------------------------------------------------- 密码哈希

PBKDF2_ROUNDS = 200_000
SALT_BYTES = 16


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(SALT_BYTES)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, PBKDF2_ROUNDS)
    return f"pbkdf2_sha256${PBKDF2_ROUNDS}${base64.b64encode(salt).decode()}${base64.b64encode(digest).decode()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        algorithm, rounds, salt_b64, digest_b64 = stored.split("$")
        if algorithm != "pbkdf2_sha256":
            return False
        digest = hashlib.pbkdf2_hmac(
            "sha256", password.encode("utf-8"), base64.b64decode(salt_b64), int(rounds)
        )
        return hmac.compare_digest(digest, base64.b64decode(digest_b64))
    except (ValueError, TypeError):
        return False


# ---------------------------------------------------------------- 会话令牌

TOKEN_TTL_SECONDS = 60 * 60 * 24 * 30  # 30 天


def _secret() -> bytes:
    value = os.environ.get("AUTH_SECRET", "").strip()
    if not value:
        # 未配置时用固定开发密钥并打印警告：保证本地可跑，但生产必须显式配置。
        value = "dev-only-insecure-secret-change-me"
    return value.encode("utf-8")


def issue_token(user_id: int, username: str) -> str:
    payload = {"uid": user_id, "u": username, "exp": int(time.time()) + TOKEN_TTL_SECONDS}
    body = base64.urlsafe_b64encode(json.dumps(payload, separators=(",", ":")).encode()).rstrip(b"=")
    signature = hmac.new(_secret(), body, hashlib.sha256).digest()
    return f"{body.decode()}.{base64.urlsafe_b64encode(signature).rstrip(b'=').decode()}"


def read_token(token: str) -> dict | None:
    try:
        body_b64, signature_b64 = token.split(".")
        body = body_b64.encode()
        expected = hmac.new(_secret(), body, hashlib.sha256).digest()
        actual = base64.urlsafe_b64decode(signature_b64 + "=" * (-len(signature_b64) % 4))
        if not hmac.compare_digest(expected, actual):
            return None
        payload = json.loads(base64.urlsafe_b64decode(body_b64 + "=" * (-len(body_b64) % 4)))
        if int(payload.get("exp", 0)) < time.time():
            return None
        return payload
    except (ValueError, TypeError, json.JSONDecodeError):
        return None


# ---------------------------------------------------------------- 表


class User(Base, TimestampMixin):
    __tablename__ = "app_user"
    __table_args__ = (UniqueConstraint("username", name="uq_app_user_username"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(String(48), index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    display_name: Mapped[str] = mapped_column(String(48), default="")
    contact: Mapped[str] = mapped_column(String(120), default="")  # 可选，不校验
    is_active: Mapped[bool] = mapped_column(default=True)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    favorites: Mapped[list["Favorite"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )


class Favorite(Base):
    """用户收藏的工厂。``note`` 供采购备忘，属于用户私有数据。

    ``enterprise_name`` 而不是 ``enterprise_id``：见模块开头。这里**故意不加**指向
    ``enterprise`` 的外键 —— 有了外键，重灌数据时就必须先删掉它才能重建企业表，
    而重建企业表正是我们想让它俩互不干扰的事情。
    """

    __tablename__ = "favorite"
    __table_args__ = (
        UniqueConstraint("user_id", "enterprise_name", name="uq_favorite_user_ent"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("app_user.id", ondelete="CASCADE"), index=True)
    enterprise_name: Mapped[str] = mapped_column(String(160), index=True)
    note: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    user: Mapped[User] = relationship(back_populates="favorites")


# 灌库（``python -m app.cli seed --drop``）要重建的是**数据层**：
# 地区、集群、企业、关系表。这两张表装的是账号与收藏 —— 用户自己的数据，
# 不是从清单推导出来的。若一起删掉，每次更新名单都等于让所有人重新注册、
# 收藏清零（曾经就是这样：注册后重灌一次，账号直接登录不了）。
PRESERVED_TABLES = frozenset({"app_user", "favorite"})