#!/usr/bin/env bash
#
# 浙江产业带企业库 · 启停脚本
#
#   ./zanzhu.sh start      启动（默认：确认数据库 → 起后端 → 起前端 → 等就绪）
#   ./zanzhu.sh stop       停止（按 PID 停我们起的进程，逐个确认已退出）
#   ./zanzhu.sh restart    重启
#   ./zanzhu.sh status     查看状态（端口 / 进程 / PID 文件）
#   ./zanzhu.sh logs [api|web] [行数]
#
# 三条设计原则（都是踩出来的）：
#
# 1. **先问端口，不信 PID 文件。** PID 会被系统回收，一个陈旧的 PID 文件加上
#    一个恰好复用了该 PID 的无关进程，就是「脚本说在跑、实际端口连不上」。
#    所以「谁在占端口」是唯一判据，PID 文件只用来找到我们自己起的进程。
# 2. **按「进程树 + 端口」收尾，不靠进程组。** uvicorn --reload 与 vite 都会派生子进程
#    （热重载 worker、esbuild），只 kill 父进程会留下占着端口的孤儿 —— 下次启动报
#    「地址已占用」，而 `ps` 里那个进程的名字看起来完全正常。macOS 没有 setsid，
#    而非交互 shell 的子进程与脚本同组，发整组信号会连脚本自己一起打死，
#    所以这里逐个收集子孙进程并配合「谁占着端口」来收。
# 3. **只停自己起的。** 端口被别的项目占用时（本机 8020 上就常驻着另一个服务），
#    脚本停下来报错并说清是谁，绝不 `kill` 一个不认识的进程。
#
# 所有运行产物都在 .run/ 下（PID、日志），该目录已在 .gitignore 里。

set -uo pipefail

# 依赖的外部命令：缺任何一个都会让「谁在占端口」判断失灵，
# 而不失灵比报错更难查 —— 比如没有 lsof 时脚本会认为端口空着，
# 于是又起一个抢端口的服务。所以开头就逐个确认。
for cmd in lsof pgrep curl ps awk; do
  command -v "$cmd" >/dev/null 2>&1 || {
    printf '缺少命令 %s，无法判断端口占用情况（macOS 自带，Linux 需装 lsof）\n' "$cmd" >&2
    exit 1
  }
done

# psql 只用来「探目标库通不通」。缺了它不影响起服务，所以不硬性要求 ——
# 但必须显式说明判据降级了，否则又会回到「按端口猜数据库」的老路上。
PSQL="$(command -v psql || true)"

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

RUN_DIR="$ROOT/.run"
API_PID_FILE="$RUN_DIR/api.pid"
WEB_PID_FILE="$RUN_DIR/web.pid"
API_LOG="$RUN_DIR/api.log"
WEB_LOG="$RUN_DIR/web.log"

API_PORT="${API_PORT:-5172}"
WEB_PORT="${WEB_PORT:-5173}"
API_HOST="${API_HOST:-127.0.0.1}"
WEB_HOST="${WEB_HOST:-127.0.0.1}"

VENV="$ROOT/.venv"
PY="$VENV/bin/python"
BACKEND="$ROOT/backend"
FRONTEND="$ROOT/frontend"

# 本脚本**能启动**的那套独立集群。注意它只是「脚本管得着的一份」，
# 不等于后端实际连的库 —— 后者由 backend/.env 的 DATABASE_URL 决定（见下面
# 的 load_db_target）。两者分开，是因为后端早就可能指向 Postgres.app 或容器里的库。
PG_DATA="${PG_DATA:-$HOME/.local/share/zanzhu-pg}"
PG_PORT="${PG_PORT:-5433}"
PG_CTL="${PG_CTL:-$(command -v pg_ctl || echo /usr/local/opt/postgresql@18/bin/pg_ctl)}"

WAIT_SECONDS="${WAIT_SECONDS:-40}"

# ── 输出 ────────────────────────────────────────────────────────────────
if [ -t 1 ]; then
  C_DIM=$'\033[2m'; C_RED=$'\033[31m'; C_GREEN=$'\033[32m'; C_YELLOW=$'\033[33m'; C_OFF=$'\033[0m'
else
  C_DIM=""; C_RED=""; C_GREEN=""; C_YELLOW=""; C_OFF=""
fi
info()  { printf '%s\n' "$*"; }
dim()   { printf '%s%s%s\n' "$C_DIM" "$*" "$C_OFF"; }
ok()    { printf '%s✓%s %s\n' "$C_GREEN" "$C_OFF" "$*"; }
warn()  { printf '%s!%s %s\n' "$C_YELLOW" "$C_OFF" "$*"; }
fail()  { printf '%s✗%s %s\n' "$C_RED" "$C_OFF" "$*" >&2; }
die()   { fail "$*"; exit 1; }

# ── 端口 / 进程 ─────────────────────────────────────────────────────────
# 谁在监听这个端口。返回若干 PID（可能不止一个：父进程 + worker）。
port_pids() {
  lsof -nP -iTCP:"$1" -sTCP:LISTEN -t 2>/dev/null | sort -u
}

port_in_use() {
  [ -n "$(port_pids "$1")" ]
}

# 这个端口在监听，且进程属于本仓库
port_is_ours() {
  local pid
  for pid in $(port_pids "$1"); do
    proc_is_ours "$pid" && return 0
  done
  return 1
}

# 这个进程是不是本库的服务？用它把「我们起的」和「别人的服务」分开。
#
# 两道判据都要满足，缺一不可：
#
#   * **命令行看起来是本库的服务**（uvicorn app.main / vite），或路径明确指向本仓库；
#   * **工作目录在本仓库里** —— uvicorn 以相对路径 ../.venv/bin/python 启动，
#     命令行里看不见仓库路径，只能靠工作目录认。
#
# 只看工作目录会把「从本目录起的任何东西」都当成自己人：一个临时
# `python -m http.server` 就能让脚本认为端口上跑的是我们的服务，
# 进而在停止时误杀它。只看命令行又会把相对路径启动的 uvicorn 判成外人。
proc_is_ours() {
  local pid="$1" cmd cwd
  cmd="$(ps -o command= -p "$pid" 2>/dev/null)" || return 1
  cwd="$(lsof -a -p "$pid" -d cwd -Fn 2>/dev/null | sed -n 's/^n//p')" || true

  case "$cmd" in
    *"$ROOT"*) return 0 ;;                     # 命令行里直接带着仓库路径
    *uvicorn*app.main*|*uvicorn*app.main*) return 0 ;;
    *vite*) return 0 ;;
  esac

  # 命令行认不出来：要求工作目录在仓库里**且**是本库的入口形态
  case "$cwd" in
    "$ROOT"|"$ROOT"/*)
      case "$cmd" in
        *uvicorn*|*vite*|*"npm run dev"*) return 0 ;;
      esac
      ;;
  esac
  return 1
}

proc_alive() {
  [ -n "${1:-}" ] && kill -0 "$1" 2>/dev/null
}

# 列出占用端口但不属于我们的进程（用于给出可读的报错）
describe_foreign() {
  local pid
  for pid in $(port_pids "$1"); do
    proc_is_ours "$pid" && continue
    printf '    PID %s  %s\n' "$pid" "$(ps -o command= -p "$pid" 2>/dev/null | cut -c1-110)"
  done
}

read_pid() {
  local file="$1" pid
  [ -f "$file" ] || return 1
  pid="$(tr -dc '0-9' < "$file" 2>/dev/null)"
  [ -n "$pid" ] || return 1
  printf '%s' "$pid"
}

# 一个进程的所有子孙（BFS）。热重载会把真正的服务放在子进程里 ——
# uvicorn --reload 的 reloader 进程只是父级，vite 的 esbuild 也一样。
# 只 kill 父进程会留下占着端口的孤儿，下次启动报「地址已占用」，
# 而 `ps` 里那个进程的名字看起来完全正常。
descendants() {
  local pid="$1" child
  for child in $(pgrep -P "$pid" 2>/dev/null); do
    descendants "$child"
    printf '%s\n' "$child"
  done
}

# 我们要停掉的全部进程：PID 文件里的那棵树 + 当前占着端口的所有进程。
# 两个来源都要 —— 前者覆盖「刚起的、还没监听端口」的情况，
# 后者覆盖「PID 文件丢了/过期了但端口确实被我们占着」的情况。
targets_for() {
  local port="$1" pidfile="$2" pid extra
  {
    if pid="$(read_pid "$pidfile")" && proc_alive "$pid"; then
      printf '%s\n' "$pid"
      descendants "$pid"
    fi
    for extra in $(port_pids "$port"); do
      proc_is_ours "$extra" && printf '%s\n' "$extra"
    done
  } | awk 'NF' | sort -u
}

stop_group() {
  local label="$1" port="$2" pidfile="$3"
  local targets waited=0 pid

  targets="$(targets_for "$port" "$pidfile")"
  if [ -z "$targets" ]; then
    rm -f "$pidfile"
    dim "  $label 本来就没在跑"
    return 0
  fi

  dim "  停止 ${label}（$(printf '%s ' $targets)）"
  # 先 TERM：让 uvicorn / vite 有机会自己清理（关连接、删 socket）。
  # 顺序无所谓 —— 信号发给整棵树，父进程退出不会让子进程收不到。
  for pid in $targets; do kill -TERM "$pid" 2>/dev/null; done

  # 判据是**端口释放**，不是 PID 消失：父进程先退、worker 还在的时候，
  # 只看 PID 会以为已经停好了。
  while [ "$waited" -lt 30 ]; do
    port_in_use "$port" || break
    sleep 1
    waited=$((waited + 1))
  done

  # 还占着端口：再点一次名，仍属于我们的才强杀
  if port_in_use "$port"; then
    warn "  $label 端口 ${port} 仍未释放，强制结束"
    for pid in $(targets_for "$port" "$pidfile"); do kill -KILL "$pid" 2>/dev/null; done
    waited=0
    while [ "$waited" -lt 10 ] && port_in_use "$port"; do sleep 1; waited=$((waited + 1)); done
  fi

  rm -f "$pidfile"
  if port_in_use "$port"; then
    fail "  $label 没有停下来，端口 ${port} 仍被占用："
    describe_foreign "$port"
    return 1
  fi
  ok "$label 已停止"
  return 0
}

# ── PostgreSQL ──────────────────────────────────────────────────────────
#
# 后端连的是**哪一个**库，由 backend/.env 的 DATABASE_URL 决定，不是本脚本的
# PG_PORT。两者混为一谈会出两种错，都能把人带到沟里：
#
#   * 把「5433 在跑」当成「数据库就绪」，而后端其实连 5432 —— 启动照报「全部就绪」，
#     打开页面却没有数据（或数据是另一份）；
#   * 后端连的是外部库（Postgres.app / 容器 / 远端），脚本却去起一份本地集群，
#     起完还占着一个没人用的端口，下次再有人用 5433 就跑不动。
#
# 所以这里把「后端连哪个库」解析出来，按它的主机/端口去判可达性；
# 只有目标**恰好就是本脚本管的那份集群**时，才由脚本负责启动它。
DB_URL=""            # 后端实际使用的连接串（从 backend/.env 读，供判断与展示）
DB_HOST="127.0.0.1"
DB_PORT=""
DB_USER=""
DB_NAME="postgres"
# 密码单独存、只经环境变量传给 psql —— 放进命令行会被同机其他用户从 `ps`
# 里读到（本库是公开仓库，不能让口令出现在日志或进程列表里）。
DB_PASS=""

load_db_target() {
  local line
  [ -f "$BACKEND/.env" ] || return 0
  line="$(sed -n 's/^DATABASE_URL=//p' "$BACKEND/.env" | head -1 | tr -d '\r')"
  [ -n "$line" ] || return 0
  DB_URL="$line"

  # 形如 postgresql+psycopg://user:pass@host:port/dbname
  local rest="${line#*://}"             # user:pass@host:port/dbname
  local dbname="${rest##*/}"            # dbname
  rest="${rest%%/*}"                    # user:pass@host:port
  local creds="" authority="$rest"
  case "$rest" in
    *@*) creds="${rest%@*}"; authority="${rest##*@}" ;;   # user:pass
  esac
  if [ -n "$creds" ]; then
    DB_USER="${creds%%:*}"
    case "$creds" in *:*) DB_PASS="${creds#*:}" ;; esac
  fi
  DB_HOST="${authority%%:*}"
  case "$authority" in
    *:*) DB_PORT="${authority##*:}" ;;
    *)   DB_PORT="" ;;
  esac
  [ -n "$DB_HOST" ] || DB_HOST="127.0.0.1"
  [ -n "$dbname" ] && DB_NAME="$dbname"
}

# 目标库就是本脚本管得着的那份本地集群吗（同主机同端口）。
db_target_is_bundled() {
  [ -n "$DB_PORT" ] || return 1
  case "$DB_HOST" in
    127.0.0.1|localhost|::1) [ "$DB_PORT" = "$PG_PORT" ] ;;
    *) return 1 ;;
  esac
}

# 目标库端口通不通。用 psql 只做「能不能建连接」，不查任何表 ——
# 空库（还没灌数据）也是「可达」，这与「有没有数据」是两件事。
db_reachable() {
  [ -n "$DB_PORT" ] || return 1
  # 没有 psql 时退到「端口是否在监听」—— 弱一层，但比直接判死强，
  # 且 ensure_postgres 会把降级这件事说出来。
  if [ -z "$PSQL" ]; then
    port_in_use "$DB_PORT"
    return
  fi
  # 口令走 PGPASSWORD 环境变量，不进命令行（见 load_db_target 里的说明）。
  # 连 `postgres` 维护库而不是业务库：目标库可能还没建表，「可达」与
  # 「有没有数据」是两件事，不该混。
  PGPASSWORD="$DB_PASS" "$PSQL" -h "$DB_HOST" -p "$DB_PORT" \
    ${DB_USER:+-U "$DB_USER"} -d postgres -c "select 1" >/dev/null 2>&1
}

pg_running() {
  port_in_use "$PG_PORT"
}

ensure_postgres() {
  load_db_target

  # 没有 .env 或 .env 里没写 DATABASE_URL：后端会落到 config.py 的默认值，
  # 那是另一套地址，脚本无从判断 —— 说清楚，不要装作已经确认过。
  if [ -z "$DB_PORT" ]; then
    warn "backend/.env 里没有 DATABASE_URL，无法确认后端连的是哪个库"
    dim  "  后端将使用 app/config.py 的默认值；下面只保证本脚本管的那份集群在跑"
  elif db_target_is_bundled; then
    dim "  后端连的是本脚本管理的集群（${DB_HOST}:${DB_PORT}）"
  else
    # 外部库：只探测可达性，绝不试图启动或占用它 —— 那不是我们的数据目录。
    dim "  后端连的是外部数据库（${DB_HOST}:${DB_PORT}），本脚本不会启动它"
    if db_reachable; then
      ok "数据库可达（${DB_HOST}:${DB_PORT}）"
      return 0
    fi
    fail "数据库连不上（${DB_HOST}:${DB_PORT}），而它不由本脚本管理"
    dim  "  请先自行启动它（Postgres.app / 容器 / 远端），或把 backend/.env 的"
    dim  "  DATABASE_URL 改成 postgresql+psycopg://<user>@127.0.0.1:${PG_PORT}/zanzhu"
    dim  "  让本脚本负责这份本地集群。"
    return 1
  fi

  if pg_running; then
    dim "  数据库已在运行（端口 ${PG_PORT}）"
    return 0
  fi

  [ -d "$PG_DATA" ] || die "找不到数据库数据目录 ${PG_DATA}（首次需按 README 初始化）"
  [ -x "$PG_CTL" ] || die "找不到 pg_ctl（可用 PG_CTL=/path/to/pg_ctl 指定）"

  # -w 等启动完成：不等的话后端的第一次连接会失败，看起来像「数据库没数据」。
  dim "  启动数据库（${PG_DATA}，端口 ${PG_PORT}）"
  if ! "$PG_CTL" -D "$PG_DATA" -o "-p $PG_PORT" -l "$RUN_DIR/postgres.log" -w start >/dev/null 2>&1; then
    fail "数据库启动失败，日志：$RUN_DIR/postgres.log"
    tail -n 15 "$RUN_DIR/postgres.log" 2>/dev/null | sed 's/^/    /'
    return 1
  fi
  ok "数据库已启动（端口 ${PG_PORT}）"
  return 0
}

# ── 后端 / 前端 ─────────────────────────────────────────────────────────
# 就绪 ≠ 「端口上有东西回 200」。本机 8020 上常年跑着另一个 Python 服务，
# 而 `python -m http.server` 也回 200 —— 只看状态码会把它们当成我们的服务，
# 然后报「全部就绪」，点开却是别人的页面。判据要落在**服务自己吐出来的内容**上。
#
# 两个判据故意取**不同**的字串，各取自己那侧的稳定标识：
#   * 后端用 settings.site_name（`/api/health` 回的那句，见 backend/app/config.py）；
#   * 前端用标签页标题里的站名（`frontend/index.html` 的静态 <title>，
#     JS 启动后由 `src/main.js` 的 SITE_TITLE 改成同一个后缀）。
# 前端**不能**再用「浙江产业带企业库」：那个名字现在是页面内的品牌文案，
# 由 JS 渲染，`curl` 只拿得到 index.html，抓不到它。
API_MARKER="${API_MARKER:-浙江产业带企业库}"
WEB_MARKER="${WEB_MARKER:-杭州波动粒子}"

api_ready() {
  local body
  body="$(curl -fsS -m 3 "http://$API_HOST:$API_PORT/api/health" 2>/dev/null)" || return 1
  case "$body" in
    *"$API_MARKER"*) return 0 ;;
  esac
  return 1
}

web_ready() {
  local body
  body="$(curl -fsS -m 3 "http://$WEB_HOST:$WEB_PORT/" 2>/dev/null)" || return 1
  case "$body" in
    *"$WEB_MARKER"*) return 0 ;;
  esac
  return 1
}

start_api() {
  if port_in_use "$API_PORT"; then
    if api_ready; then
      dim "  后端已在运行（端口 ${API_PORT}，健康检查通过）"
      return 0
    fi
    fail "端口 ${API_PORT} 上有别的东西在跑（不是本库后端）："
    describe_foreign "$API_PORT"
    die "  请先让出端口，或用 API_PORT=8040 ./zanzhu.sh start 换一个"
  fi

  [ -x "$PY" ] || die "找不到虚拟环境 ${VENV}（先按 README 建 venv 并装 requirements）"
  [ -f "$BACKEND/.env" ] || warn "没有 backend/.env —— 管理接口与令牌签名会用开发默认值"

  # AUTH_SECRET 未设置时后端会落到内置开发密钥并告警；这里显式给一个，
  # 是为了让重启后**已登录的会话仍然有效**（密钥一变，所有令牌立刻作废）。
  : "${AUTH_SECRET:=dev-auth-secret}"

  # 管理令牌：优先用环境变量，其次读 backend/.env（用户把令牌写在那里最自然）。
  # 注意顺序 —— 一旦传了 ADMIN_TOKEN 环境变量，它会**覆盖** .env 里的值，
  # 所以这里只在「环境变量没设」时才从 .env 取，避免把用户配好的令牌顶掉。
  [ -n "${ADMIN_TOKEN:-}" ] && ADMIN_TOKEN_FROM_ENV=1
  if [ -z "${ADMIN_TOKEN:-}" ] && [ -f "$BACKEND/.env" ]; then
    ADMIN_TOKEN="$(sed -n 's/^ADMIN_TOKEN=//p' "$BACKEND/.env" | head -1 | tr -d '\r' | tr -d ' ')"
  fi
  if [ -n "${ADMIN_TOKEN:-}" ]; then
    dim "  管理接口已启用（令牌来自 $([ -n "${ADMIN_TOKEN_FROM_ENV:-}" ] && echo 环境变量 || echo backend/.env)）"
  else
    dim "  管理接口未启用（backend/.env 里 ADMIN_TOKEN 为空）"
  fi

  # 普通后台启动，PID 记进 .run/ 供停止时定位；停止用的是「进程树 + 端口」
  dim "  启动后端 → http://$API_HOST:$API_PORT"
  (
    cd "$BACKEND" || exit 1
    AUTH_SECRET="$AUTH_SECRET" ADMIN_TOKEN="${ADMIN_TOKEN:-}" exec "$PY" -m uvicorn app.main:app \
      --host "$API_HOST" --port "$API_PORT" --reload
  ) >>"$API_LOG" 2>&1 &
  echo $! > "$API_PID_FILE"

  local waited=0
  while [ "$waited" -lt "$WAIT_SECONDS" ]; do
    api_ready && { ok "后端就绪（HTTP，PID $(cat "$API_PID_FILE")）"; return 0; }
    sleep 1
    waited=$((waited + 1))
  done

  fail "后端在 $WAIT_SECONDS 秒内没有就绪，日志末尾："
  tail -n 20 "$API_LOG" 2>/dev/null | sed 's/^/    /'
  return 1
}

start_web() {
  if port_in_use "$WEB_PORT"; then
    if web_ready; then
      dim "  前端已在运行（端口 ${WEB_PORT}）"
      return 0
    fi
    fail "端口 ${WEB_PORT} 上有别的东西在跑（不是本库前端）："
    describe_foreign "$WEB_PORT"
    die "  请先让出端口，或用 WEB_PORT=5273 ./zanzhu.sh start 换一个"
  fi

  [ -d "$FRONTEND/node_modules" ] || die "前端依赖未安装（cd frontend && npm install）"

  dim "  启动前端 → http://$WEB_HOST:$WEB_PORT"
  (
    cd "$FRONTEND" || exit 1
    exec npm run dev -- --port "$WEB_PORT" --host "$WEB_HOST"
  ) >>"$WEB_LOG" 2>&1 &
  echo $! > "$WEB_PID_FILE"

  local waited=0
  while [ "$waited" -lt "$WAIT_SECONDS" ]; do
    web_ready && { ok "前端就绪（PID $(cat "$WEB_PID_FILE")）"; return 0; }
    sleep 1
    waited=$((waited + 1))
  done

  fail "前端在 $WAIT_SECONDS 秒内没有就绪，日志末尾："
  tail -n 20 "$WEB_LOG" 2>/dev/null | sed 's/^/    /'
  return 1
}

# ── 子命令 ──────────────────────────────────────────────────────────────
cmd_start() {
  mkdir -p "$RUN_DIR"
  info "启动 浙江产业带企业库"
  ensure_postgres || die "数据库没起来，后面的步骤没有意义"
  start_api || die "后端启动失败"
  start_web || die "前端启动失败"

  info ""
  ok "全部就绪"
  info "  站点    http://$WEB_HOST:$WEB_PORT"
  info "  接口    http://$API_HOST:$API_PORT/api/health"
  info "  文档    http://$API_HOST:$API_PORT/docs"
  dim  "  停止    ./zanzhu.sh stop    日志  ./zanzhu.sh logs"
}

cmd_stop() {
  info "停止 浙江产业带企业库"
  local rc=0
  stop_group "前端" "$WEB_PORT" "$WEB_PID_FILE" || rc=1
  stop_group "后端" "$API_PORT" "$API_PID_FILE" || rc=1

  # 数据库**不停**：它和这个脚本的启停是两件事 ——
  # 数据目录里是唯一一份数据，停它只会在下次启动时多等几十秒，没有别的好处。
  if pg_running; then dim "  数据库保持运行（端口 ${PG_PORT}）"; fi

  [ "$rc" -eq 0 ] && ok "已停止" || fail "有进程没停干净，见上面的提示"
  return "$rc"
}

cmd_status() {
  local overall=0
  info "状态 浙江产业带企业库"
  info ""

  # 数据库状态按**后端实际连的那个库**报，不按本脚本的 PG_PORT ——
  # 报了错的那个端口等于没报（见 ensure_postgres 顶上那段）。
  load_db_target
  if [ -z "$DB_PORT" ]; then
    warn "数据库  无法确认（backend/.env 里没有 DATABASE_URL）"; overall=1
  elif ! db_target_is_bundled; then
    if db_reachable; then ok "数据库  可达（${DB_HOST}:${DB_PORT}，外部库）"; else warn "数据库  连不上（${DB_HOST}:${DB_PORT}，外部库）"; overall=1; fi
  elif pg_running; then ok "数据库  运行中（端口 ${PG_PORT}）"
  else warn "数据库  未运行（端口 ${PG_PORT}）"; overall=1; fi
  if api_ready; then ok "后端    就绪（端口 ${API_PORT}）"
  elif port_in_use "$API_PORT"; then warn "后端    端口 ${API_PORT} 上是别的服务（健康检查的返回里没有站点名）"; overall=1
  else warn "后端    未运行（端口 ${API_PORT}）"; overall=1; fi
  if web_ready; then ok "前端    就绪（端口 ${WEB_PORT}）"
  elif port_in_use "$WEB_PORT"; then warn "前端    端口 ${WEB_PORT} 上是别的服务（页面里没有站点名）"; overall=1
  else warn "前端    未运行（端口 ${WEB_PORT}）"; overall=1; fi

  info ""
  local pid
  for pair in "后端:$API_PID_FILE" "前端:$WEB_PID_FILE"; do
    local label="${pair%%:*}" file="${pair#*:}"
    if pid="$(read_pid "$file")" && proc_alive "$pid"; then
      dim "  $label PID 文件：${pid}（进程组 $(ps -o pgid= -p "$pid" 2>/dev/null | tr -d ' ')）"
    elif [ -f "$file" ]; then
      warn "  $label 的 PID 文件已过期（内容 $(tr -d '\n' < "$file" 2>/dev/null)），以端口状态为准"
    fi
  done
  dim "  日志：$RUN_DIR/api.log  $RUN_DIR/web.log"
  return "$overall"
}

cmd_logs() {
  local which="${1:-api}" lines="${2:-40}"
  case "$which" in
    api|api]*|back*|后端) tail -n "$lines" "$API_LOG" 2>/dev/null || die "没有 $API_LOG" ;;
    web|front*|前端) tail -n "$lines" "$WEB_LOG" 2>/dev/null || die "没有 $WEB_LOG" ;;
    -f|follow) tail -f "$API_LOG" "$WEB_LOG" 2>/dev/null || die "没有日志文件" ;;
    *) die "用法：./zanzhu.sh logs [api|web] [行数]  或  ./zanzhu.sh logs -f" ;;
  esac
}

cmd_restart() {
  cmd_stop || warn "停止阶段有问题，仍然继续启动"
  info ""
  cmd_start
}

usage() {
  cat <<EOF
浙江产业带企业库 · 启停脚本

  ./zanzhu.sh start              启动数据库 + 后端 + 前端（已在跑的会自动跳过）
  ./zanzhu.sh stop               停止后端与前端（数据库保持运行）
  ./zanzhu.sh restart            重启
  ./zanzhu.sh status             查看状态
  ./zanzhu.sh logs [api|web] [N] 看日志（默认后端 40 行）；logs -f 跟随

可覆盖的环境变量：
  API_PORT=5172  WEB_PORT=5173  API_HOST=127.0.0.1  WEB_HOST=127.0.0.1
  PG_PORT=5433   PG_DATA=~/.local/share/zanzhu-pg   PG_CTL=/path/to/pg_ctl
  AUTH_SECRET=<令牌签名密钥>   WAIT_SECONDS=40

说明：
  * 「在不在跑」只看端口，不只看 PID 文件；只停止属于本仓库的进程。
  * 后端连哪个库由 backend/.env 的 DATABASE_URL 决定。目标不是本脚本那份本地集群时
    （如 Postgres.app / 容器 / 远端），脚本只探测可达性，不会去启动或占用它。
  * 数据库与前后端分开管理：stop 不会停数据库（数据目录里是唯一一份数据）。
  * 运行产物都在 .run/ 下（PID、日志），已在 .gitignore 里。
EOF
}

main() {
  local cmd="${1:-}"
  case "$cmd" in
    start)   shift; cmd_start "$@" ;;
    stop)    shift; cmd_stop "$@" ;;
    restart) shift; cmd_restart "$@" ;;
    status)  shift; cmd_status "$@" ;;
    logs)    shift; cmd_logs "$@" ;;
    ""|-h|--help|help) usage ;;
    *) fail "未知命令：$cmd"; info ""; usage; exit 2 ;;
  esac
}

main "$@"