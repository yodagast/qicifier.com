"""``python -m app.cli init-db``      create tables / drop+recreate
``python -m app.cli seed``         load source + curated data into PostgreSQL
``python -m app.cli seed --csv F`` import a CSV list, then load everything

``--csv`` 是这份 CLI 一直缺的那一环：在此之前，一份新 CSV 放进 ``docs/`` 后
**只能**走网页端导入（或另写一个 ``tools/parse_*.py``）才会变成 ``data/source``
下的产物，``seed --drop`` 对它毫无反应 —— 于是「照着文档跑完，新名单里的公司
一家都没进库」看起来像个 bug，而报告里一个字都不提。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .database import SessionLocal, engine
from .config import settings
from .models import Base
from .seed.loader import run
from .services.list_import import GRADE_PREFIX, cluster_mapping, import_list, imported_lists

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


# ---------------------------------------------------------------------------
# CSV 导入：`seed --csv` 的实现
# ---------------------------------------------------------------------------

def resolve_csv(raw: str) -> Path:
    """把命令行给的路径解析成存在的文件。

    三种写法都认：绝对路径、相对当前目录、**相对仓库根**（``docs/x.csv``）。
    第三种是必需的：文档里那条命令在 ``backend/`` 下执行，而人们在 README 里
    抄的路径都是相对仓库根的。
    """
    repo_root = settings.data_dir.parent
    candidates = [Path(raw), Path.cwd() / raw, repo_root / raw]
    for path in candidates:
        if path.is_file():
            return path.resolve()
    raise SystemExit(
        f"找不到 CSV 文件：{raw}\n"
        "  试过：\n" + "\n".join(f"    {p}" for p in candidates)
    )


def display_source_file(csv_path: Path) -> str:
    """注册表里记的来源路径：能相对仓库根就相对，否则存绝对路径。

    与既有 9 份名单同一写法（``docs/<文件名>``）—— 只存文件名的话，
    来源地图上就看不出它在哪个目录，而 ``source_file`` 是给读者核对的唯一线索。
    """
    try:
        return str(csv_path.relative_to(settings.data_dir.parent))
    except ValueError:
        return str(csv_path)


def registry_entry_for(csv_path: Path) -> dict:
    """这份 CSV 是不是已经登记过？按**文件名**匹配注册表的 ``source_file``。

    匹配上就沿用它的 ``source_key / list_name / grade / used_for / role``，
    所以「重导一份已登记的名单」只要 ``seed --csv docs/x.csv`` 一条命令，
    不必把当初填的那一堆元数据再抄一遍 —— 抄错一个字就是另一个 ``source_key``，
    结果是同一份名单在库里出现两条、企业被拆到两边。
    """
    name = csv_path.name
    for entry in imported_lists():
        if Path(entry.get("source_file", "")).name == name:
            return entry
    return {}


def import_csv(
    raw_path: str,
    *,
    source_key: str = "",
    list_name: str = "",
    grade: str = "",
    used_for: str = "",
    role: str = "",
    default_cluster_slug: str = "",
    dry_run: bool = False,
) -> dict:
    """一份 CSV → 三份产物 + 注册表条目（与网页端导入是**同一个函数**）。

    元数据取值顺序：命令行 > 注册表里已登记的那一份 > 默认值。
    认不出来的 CSV 必须显式给 ``source_key`` —— 这个键同时是产物的文件名、
    注册表的唯一键、以及「再导一次是覆盖还是新增」的判据，不能猜。
    """
    csv_path = resolve_csv(raw_path)
    known = registry_entry_for(csv_path)

    source_key = (source_key or known.get("source_key") or "").strip()
    if not source_key:
        raise SystemExit(
            f"这份 CSV 还没登记过，需要给它一个 source_key：{csv_path.name}\n"
            "  source_key 是产物的文件名、也是注册表的唯一键；"
            "以后同名再导一次就是**覆盖**这份名单。\n"
            f"  例：python -m app.cli seed --drop --csv {raw_path} "
            '--source-key my_list --list-name "我的名单（第三方调研）"'
        )
    list_name = (list_name or known.get("list_name") or "").strip()
    if not list_name:
        raise SystemExit("缺少 --list-name：来源声明里要写清这份名单叫什么")
    grade = (grade or known.get("grade") or "site_curated").strip()
    if grade not in GRADE_PREFIX:
        raise SystemExit(f"未知核实程度 {grade!r}（可用：{'、'.join(GRADE_PREFIX)}）")

    try:
        result = import_list(
            text=csv_path.read_text(encoding="utf-8-sig"),
            list_name=list_name,
            grade=grade,
            source_key=source_key,
            source_file=display_source_file(csv_path),
            used_for=used_for or known.get("used_for", ""),
            role=role or known.get("role", ""),
            default_cluster_slug=default_cluster_slug or (known.get("default_cluster") or ""),
            dry_run=dry_run,
        )
    except ValueError as exc:
        raise SystemExit(f"导入失败：{exc}") from exc

    report = result["report"]
    report["cluster_mapping"] = cluster_mapping(result["parsed"]["rows"])
    report["source_key"] = source_key
    print_import_report(csv_path, report, registry_hit=bool(known))
    return {"report": report, "parsed": result["parsed"]}


def print_import_report(csv_path: Path, report: dict, *, registry_hit: bool) -> None:
    """把导入报告打成终端能读的样子。

    最要紧的是**落位那一段**：认不出来的「产业带 / 块状经济」取值不会报错、
    也不会挡下导入，只是那些企业不进任何集群页 —— 这个库里已经因此丢过两次
    （355 行、1069 行），所以它必须出现在终端上，而不是只躺在 JSON 报告里。
    """
    mapping = report["cluster_mapping"]
    print(f"\n导入 {csv_path.name}")
    print(
        f"  名单        {report['list_name']}（source_key={report['source_key']}，"
        f"{'沿用注册表里的元数据' if registry_hit else '新登记'}）"
    )
    print(f"  行 / 企业   {report['rows']} 行 / {report['companies']} 家")
    print(f"  档位        {' '.join(f'{k} {v}' for k, v in report['tiers'].items())}")
    print(
        f"  官网 {report['with_website']} · 电话 {report['with_phone']} · "
        f"地址 {report['with_address']} · 省外 {report['out_of_province']}"
    )
    print(
        f"  集群落位    {mapping['resolved_rows']} / {mapping['total_rows']} 行"
        f"（{'、'.join(mapping['bases']) or '无归属列'}）"
    )
    unresolved = mapping["unresolved"]
    if unresolved:
        missing = sum(group["unresolved_rows"] for group in unresolved)
        print(f"\n  ⚠️  {missing} 行没有集群归属 —— 这些企业会进名录，但不出现在任何集群页上：")
        for group in unresolved[:12]:
            print(f"       {group['label'][:40]:42s} {group['unresolved_rows']:5d} 行")
        if len(unresolved) > 12:
            print(f"       …… 另有 {len(unresolved) - 12} 个取值未列出")
        print(
            "     原因通常是「产业带 / 块状经济」列的取值还没有对应档案。\n"
            "     先给这些取值建档（见 tools/build_*_clusters.py），再重导一次。"
        )
    if report.get("warnings"):
        print("\n  提示：")
        for warning in report["warnings"]:
            print(f"    - {warning}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="app.cli")
    sub = parser.add_subparsers(dest="command", required=True)

    init = sub.add_parser("init-db", help="create tables")
    init.add_argument("--drop", action="store_true", help="drop existing tables first")

    seed = sub.add_parser("seed", help="load data")
    seed.add_argument("--drop", action="store_true", help="recreate schema before loading")
    seed.add_argument(
        "--csv",
        action="append",
        default=[],
        metavar="PATH",
        help="导入一份名单 CSV（可重复）。路径可相对当前目录，也可相对仓库根（docs/x.csv）",
    )
    seed.add_argument(
        "--source-key",
        default="",
        help="--csv 的名单键（产物文件名 + 注册表唯一键）；已登记的名单可省略",
    )
    seed.add_argument("--list-name", default="", help="--csv 的名单全名；已登记的名单可省略")
    seed.add_argument(
        "--grade",
        default="",
        help="--csv 的核实程度（official / gov_open / media / site_curated / self_reported）",
    )
    seed.add_argument("--used-for", default="", help="--csv 的用途说明（写进来源声明）")
    seed.add_argument("--role", default="", help="--csv 在库里的角色（来源地图那一列）")
    seed.add_argument(
        "--default-cluster-slug",
        default="",
        help="--csv 整份清单声明属于某条产业带时用它；逐行自带的归属优先于它",
    )
    seed.add_argument(
        "--dry-run",
        action="store_true",
        help="--csv 只解析并打印报告，不写产物、不灌库",
    )

    args = parser.parse_args(argv)

    if args.command == "init-db":
        init_db(drop=args.drop)
        return 0

    if args.command == "seed":
        # 一份 --source-key 套到多份 CSV 上会把它们**全部写进同一条注册表记录**
        # （后一份覆盖前一份，前一份的产物从此没人引用）。多文件时每份的元数据
        # 只能来自注册表，所以要求分开跑，而不是猜一个分配方式。
        if len(args.csv) > 1 and any(
            (args.source_key, args.list_name, args.grade, args.used_for, args.role, args.default_cluster_slug)
        ):
            raise SystemExit(
                f"--csv 给了 {len(args.csv)} 份，但同时给了元数据参数（--source-key / "
                "--list-name / --grade …）。\n"
                "  一条元数据会被套到每一份上，把它们全写进同一条注册表记录。\n"
                "  多份已经登记过的名单：去掉这些参数直接跑（元数据从注册表读）。\n"
                "  要登记新名单：一次只给 --csv 一份。"
            )
        for raw in args.csv:
            import_csv(
                raw,
                source_key=args.source_key,
                list_name=args.list_name,
                grade=args.grade,
                used_for=args.used_for,
                role=args.role,
                default_cluster_slug=args.default_cluster_slug,
                dry_run=args.dry_run,
            )

        if args.dry_run:
            print(
                "\n干跑结束：未写入任何文件，也没有灌库。"
                if args.csv
                else "没有 --csv，--dry-run 无事可做。"
            )
            return 0

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