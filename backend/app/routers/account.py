"""``/api/account`` — 注册 / 登录 / 收藏。

注册按需求**不需邮箱或短信验证**，因此把安全性放在别处：

* 用户名唯一 + 格式强校验（字母/数字/下划线/汉字，3-24 位）
* 密码最少 8 位，且不能是纯数字或与用户名相同
* 密码 PBKDF2-SHA256 加盐哈希；会话用签名令牌（HMAC + 过期时间）
* 登录失败做**固定时间**比较 + 简单的失败计数，避免被当作密码探测器

收藏是用户私有数据：需要登录，且只能读写自己的收藏。

收藏按**企业名称**存：``seed --drop`` 重建数据层时自增 id 会变，按 id 存的收藏
会在重灌后指向另一家企业（用户会看到自己没收藏过的工厂）。名称是本库唯一且稳定的
事实键，也是页面上展示的那个字符串。名单若删掉某家企业，那条收藏不会静默消失，
而是作为 ``stale`` 明说，并提供一次性清理。
"""

from __future__ import annotations

import re
import time
from collections import defaultdict, deque
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from ..auth import Favorite, User, hash_password, issue_token, read_token, verify_password
from ..database import get_session
from ..models import Enterprise
from ..services.queries import enterprise_cluster_names, to_enterprise_summary

router = APIRouter(prefix="/api/account", tags=["account"])

USERNAME_RE = re.compile(r"^[A-Za-z0-9_\u4e00-\u9fa5]{3,24}$")
COMMON_PASSWORDS = {"12345678", "password", "123456789", "qwertyui", "11111111", "88888888"}


class RegisterIn(BaseModel):
    username: str = Field(..., min_length=3, max_length=24)
    password: str = Field(..., min_length=8, max_length=128)
    display_name: str = Field("", max_length=48)
    contact: str = Field("", max_length=120)


class LoginIn(BaseModel):
    username: str
    password: str


class FavoriteIn(BaseModel):
    enterprise_id: int
    note: str = Field("", max_length=500)


class FavoriteNoteIn(BaseModel):
    note: str = Field("", max_length=500)


class UserOut(BaseModel):
    id: int
    username: str
    display_name: str
    contact: str = ""
    favorite_count: int = 0
    # 收藏了、但企业已不在名单里的条数（不展示内容，只告知）
    stale_count: int = 0


def known_enterprise_names(session: Session, names: list[str]) -> set[str]:
    if not names:
        return set()
    rows = session.scalars(select(Enterprise.name).where(Enterprise.name.in_(set(names)))).all()
    return set(rows)


def resolve_enterprise(session: Session, enterprise_id: int) -> Enterprise:
    enterprise = session.get(Enterprise, enterprise_id)
    if enterprise is None:
        raise HTTPException(status_code=404, detail="企业不存在")
    return enterprise


def current_user(
    authorization: str | None = Header(default=None),
    session: Session = Depends(get_session),
) -> User:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="请先登录")
    payload = read_token(authorization.split(" ", 1)[1].strip())
    if not payload:
        raise HTTPException(status_code=401, detail="登录已过期，请重新登录")
    user = session.get(User, int(payload["uid"]))
    if user is None or not user.is_active:
        raise HTTPException(status_code=401, detail="账号不可用")
    return user


# 登录失败计数：按 (用户名, 来源 IP) 记时间窗，超阈值就挡一会儿。
#
# 「无需验证即可注册」是需求，但它不等于「密码可以无限猜」：没有这一层，
# 一个脚本就能把弱口令试穿。计数放在进程内存里 —— 单实例部署够用，
# 多实例时应当在网关层做，别为此引入一张会被重灌清空的表。
LOGIN_WINDOW_SECONDS = 15 * 60
LOGIN_MAX_FAILURES = 8
_login_failures: dict[tuple[str, str], deque[float]] = defaultdict(deque)


def check_login_allowed(key: tuple[str, str]) -> None:
    window = _login_failures[key]
    now = time.time()
    while window and now - window[0] > LOGIN_WINDOW_SECONDS:
        window.popleft()
    if len(window) >= LOGIN_MAX_FAILURES:
        raise HTTPException(
            status_code=429,
            detail=f"登录失败次数过多，请 {LOGIN_WINDOW_SECONDS // 60} 分钟后再试",
        )


def record_login_failure(key: tuple[str, str]) -> None:
    _login_failures[key].append(time.time())


def clear_login_failures(key: tuple[str, str]) -> None:
    _login_failures.pop(key, None)


def validate_password(password: str, username: str) -> list[str]:
    problems: list[str] = []
    if password.lower() in COMMON_PASSWORDS:
        problems.append("密码过于常见，请更换")
    if password.isdigit():
        problems.append("密码不能是纯数字")
    if password.lower() == username.lower():
        problems.append("密码不能与用户名相同")
    return problems


def user_out(user: User, session: Session) -> UserOut:
    """``favorite_count`` 只算**能在库里对上企业**的收藏。

    名单删掉某家企业后那条收藏依然存在（用户的数据不该被我们悄悄删掉），
    但它展示不出来，所以单独用 ``stale_count`` 交代，不能混进总数 ——
    否则用户会看到「收藏 5 家」却只列出 3 家。
    """
    names = session.scalars(select(Favorite.enterprise_name).where(Favorite.user_id == user.id)).all()
    known = known_enterprise_names(session, names)
    resolvable = sum(1 for name in names if name in known)
    return UserOut(
        id=user.id,
        username=user.username,
        display_name=user.display_name or user.username,
        contact=user.contact,
        favorite_count=resolvable,
        stale_count=len(names) - resolvable,
    )


@router.post("/register", status_code=201)
def register(payload: RegisterIn, session: Session = Depends(get_session)):
    username = payload.username.strip()
    if not USERNAME_RE.match(username):
        raise HTTPException(status_code=422, detail="用户名需为 3-24 位字母、数字、下划线或汉字")

    exists = session.scalar(select(User).where(func.lower(User.username) == username.lower()))
    if exists is not None:
        raise HTTPException(status_code=409, detail="该用户名已被注册")

    problems = validate_password(payload.password, username)
    if problems:
        raise HTTPException(status_code=422, detail={"problems": problems})

    user = User(
        username=username,
        password_hash=hash_password(payload.password),
        display_name=(payload.display_name or username).strip()[:48],
        contact=payload.contact.strip()[:120],
    )
    session.add(user)
    session.commit()
    session.refresh(user)
    return {"token": issue_token(user.id, user.username), "user": user_out(user, session)}


@router.post("/login")
def login(payload: LoginIn, request: Request, session: Session = Depends(get_session)):
    username = payload.username.strip()
    key = (username.lower(), request.client.host if request.client else "unknown")
    check_login_allowed(key)

    user = session.scalar(select(User).where(func.lower(User.username) == username.lower()))
    # 用户不存在时也做一次哈希计算，避免通过响应时间判断账号是否存在
    stored = user.password_hash if user else "pbkdf2_sha256$200000$AAAA$AAAA"
    ok = verify_password(payload.password, stored)
    if user is None or not ok:
        record_login_failure(key)
        raise HTTPException(status_code=401, detail="用户名或密码不正确")
    if not user.is_active:
        raise HTTPException(status_code=403, detail="账号已被停用")

    clear_login_failures(key)
    user.last_login_at = datetime.now(timezone.utc)
    session.commit()
    return {"token": issue_token(user.id, user.username), "user": user_out(user, session)}


@router.get("/me", response_model=UserOut)
def me(user: User = Depends(current_user), session: Session = Depends(get_session)):
    return user_out(user, session)


@router.get("/favorites")
def list_favorites(user: User = Depends(current_user), session: Session = Depends(get_session)):
    """收藏列表。名单里已经不存在企业，作为 ``stale`` 单独交代，而不是静默丢掉。"""
    favorites = session.scalars(
        select(Favorite).where(Favorite.user_id == user.id).order_by(Favorite.created_at.desc())
    ).all()
    by_name: dict[str, list[Favorite]] = {}
    for fav in favorites:
        by_name.setdefault(fav.enterprise_name, []).append(fav)

    enterprises = session.scalars(
        select(Enterprise).where(Enterprise.name.in_(set(by_name) or {""}))
    ).all()
    enterprise_by_name = {ent.name: ent for ent in enterprises}
    names = enterprise_cluster_names(session, [ent.id for ent in enterprises])

    items, stale = [], []
    for name, group in by_name.items():
        ent = enterprise_by_name.get(name)
        if ent is None:
            stale.append({"name": name, "note": group[0].note})
            continue
        # 同一家企业只可能有一条（uq_favorite_user_ent）
        items.append(
            {
                "note": group[0].note,
                "created_at": group[0].created_at.isoformat() if group[0].created_at else "",
                # 摘要统一走 services.queries 的那一个实现，不在路由器里再拼一份
                "enterprise": to_enterprise_summary(ent, names.get(ent.id, [])).model_dump(),
            }
        )
    return {"total": len(items), "items": items, "stale": stale}


@router.post("/favorites", status_code=201)
def add_favorite(
    payload: FavoriteIn,
    user: User = Depends(current_user),
    session: Session = Depends(get_session),
):
    enterprise = resolve_enterprise(session, payload.enterprise_id)

    existing = session.scalar(
        select(Favorite).where(
            Favorite.user_id == user.id, Favorite.enterprise_name == enterprise.name
        )
    )
    if existing is not None:
        if payload.note:
            existing.note = payload.note
            session.commit()
        return {
            "created": False,
            "enterprise_id": enterprise.id,
            "note": existing.note,
        }

    session.add(
        Favorite(user_id=user.id, enterprise_name=enterprise.name, note=payload.note)
    )
    session.commit()
    return {"created": True, "enterprise_id": enterprise.id, "note": payload.note}


@router.delete("/favorites/unresolved")
def purge_stale(user: User = Depends(current_user), session: Session = Depends(get_session)):
    """清理「企业已不在名单里」的收藏。单独一个入口：那些记录没有可用的企业 id。"""
    names = session.scalars(select(Favorite.enterprise_name).where(Favorite.user_id == user.id)).all()
    orphaned = sorted(set(names) - known_enterprise_names(session, names))
    if orphaned:
        session.execute(
            delete(Favorite).where(
                Favorite.user_id == user.id, Favorite.enterprise_name.in_(orphaned)
            )
        )
        session.commit()
    return {"removed": orphaned, "count": len(orphaned)}


@router.delete("/favorites/{enterprise_id}")
def remove_favorite(
    enterprise_id: int,
    user: User = Depends(current_user),
    session: Session = Depends(get_session),
):
    enterprise = resolve_enterprise(session, enterprise_id)
    favorite = session.scalar(
        select(Favorite).where(
            Favorite.user_id == user.id, Favorite.enterprise_name == enterprise.name
        )
    )
    if favorite is None:
        raise HTTPException(status_code=404, detail="未收藏该企业")
    session.delete(favorite)
    session.commit()
    return {"removed": True, "enterprise_id": enterprise.id}


@router.patch("/favorites/{enterprise_id}")
def update_note(
    enterprise_id: int,
    payload: FavoriteNoteIn,
    user: User = Depends(current_user),
    session: Session = Depends(get_session),
):
    enterprise = resolve_enterprise(session, enterprise_id)
    favorite = session.scalar(
        select(Favorite).where(
            Favorite.user_id == user.id, Favorite.enterprise_name == enterprise.name
        )
    )
    if favorite is None:
        raise HTTPException(status_code=404, detail="未收藏该企业")
    favorite.note = payload.note
    session.commit()
    return {"enterprise_id": enterprise.id, "note": favorite.note}


@router.get("/favorites/ids")
def favorite_ids(user: User = Depends(current_user), session: Session = Depends(get_session)):
    """仅返回 id 列表，供列表页一次性标记「已收藏」状态，避免 N+1 请求。

    存的是企业名，这里解析成**当前**的 id —— 所以重灌数据后仍然指向同一家企业。
    """
    names = session.scalars(select(Favorite.enterprise_name).where(Favorite.user_id == user.id)).all()
    if not names:
        return {"ids": []}
    ids = session.scalars(
        select(Enterprise.id).where(Enterprise.name.in_(set(names)))
    ).all()
    return {"ids": list(ids)}