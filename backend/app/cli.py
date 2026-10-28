"""``python -m app.cli init-db``   create tables / drop+recreate
``python -m app.cli seed``      load source + curated data into PostgreSQL
"""

from __future__ import annotations

import argparse
import sys

from .database import SessionLocal, engine
from .models import Base
from .seed.loader import run

# 导入即注册：User / Favorite 定义在 auth 模块里，只有该模块被导入时才会挂到
# Base.metadata 上。缺了它，`drop_all()` 就看不到这两张表（它们不是数据层，
# 但 create_all 与权限判断仍需要它们出现在 metadata 里）。
from .auth import PRESERVED_TABLES, Favorite, User  # noqa: F401


def droppable_tables() -> list:
    """要重建的**数据层**表；账号与收藏不在其中。

    `drop_all()` 一把梭会把 app_user / favorite 一起删掉 —— 于是每次更新名单，
    所有人都要重新注册、收藏清零（曾经就是这样，「登录系统坏了」是这么来的）。
    """
    return [table for name, table in Base.metadata.tables.items() if name not in PRESERVED_TABLES]


def init_db(drop: bool = False) -> None:
    if drop:
        Base.metadata.drop_all(bind=engine, tables=droppable_tables())
    Base.metadata.create_all(bind=engine)
    print("schema ready")


def reseed() -> dict:
    """重建数据层并按 ``data/`` 下的清单重新灌库。

    **账号与收藏不受影响**（见 ``droppable_tables``）—— 这是「更新名单」的语义，
    不是「从零开始」。

    单独抽成函数是为了让网页端也能走同一条路径：管理界面保存联系方式、改产业带档案、
    导入名单之后都必须重新灌库才生效，而那些页面原先只能让用户自己去终端敲命令
    （命令抄错一个字符就是一次失败的灌库），见 ``routers/admin.py`` 的 ``/reseed``。

    这里用 ``engine`` 直接建删表、再开一个**新** session 灌数据：删表与被删的表
    不能共用一个已开启的事务，否则 drop 会等自己持有的锁。
    """
    init_db(drop=True)
    with SessionLocal() as session:
        return run(session)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="app.cli")
    sub = parser.add_subparsers(dest="command", required=True)

    init = sub.add_parser("init-db", help="create tables")
    init.add_argument("--drop", action="store_true", help="drop existing tables first")

    seed = sub.add_parser("seed", help="load data")
    seed.add_argument("--drop", action="store_true", help="recreate schema before loading")

    args = parser.parse_args(argv)

    if args.command == "init-db":
        init_db(drop=args.drop)
        return 0

    if args.command == "seed":
        if args.drop:
            result = reseed()
        else:
            init_db(drop=False)
            with SessionLocal() as session:
                result = run(session)
        print(
            f"seeded regions={result['regions']} clusters={result['clusters']} "
            f"enterprises={result['enterprises']}"
        )
        return 0

    return 1


if __name__ == "__main__":
    sys.exit(main())