"""管理接口的令牌校验（``/api/admin/**`` 共用）。

单独一个模块，是为了让「人工补充」和「名单导入」两个路由都能用它，
而不必互相 import —— 路由之间互相引用会让「谁依赖谁」变得说不清。

安全设计：默认**关闭**。只有设置了 ``ADMIN_TOKEN`` 环境变量后才启用；
未设置时返回 404，不暴露这些接口存在。
"""

from __future__ import annotations

import os

from fastapi import Header, HTTPException

from ..config import settings


def admin_token() -> str:
    """管理令牌：**环境变量优先**，其次 ``backend/.env``。

    只读 ``os.environ`` 会漏掉最自然的配置方式 —— 把令牌写进 ``.env``。
    pydantic-settings 读 ``.env`` 是给它自己声明过的字段用的，**不会**把这些值
    写回 ``os.environ``；而管理令牌不是 Settings 的字段（它属于运维秘密，
    不参与应用配置），于是 ``.env`` 里的 ``ADMIN_TOKEN`` 一直不生效：
    接口永远 404，看起来像「功能没启用」，其实是「读错了地方」。
    """
    value = os.environ.get("ADMIN_TOKEN", "").strip()
    if value:
        return value
    return (getattr(settings, "admin_token", "") or "").strip()


def require_admin(x_admin_token: str | None = Header(default=None)) -> None:
    expected = admin_token()
    if not expected:
        raise HTTPException(status_code=404, detail="Not Found")
    if not x_admin_token or x_admin_token != expected:
        raise HTTPException(status_code=403, detail="管理令牌无效")
