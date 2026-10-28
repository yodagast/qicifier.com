const BASE = import.meta.env.VITE_API_BASE || '/api'

const TOKEN_KEY = 'zanzhu.token'

// 令牌在会话中途失效（过期 / 被清库）时，向会话层报一次。
// 用回调而不是 import session：api 是底层，反向依赖会形成循环。
let unauthorizedHandler = null

export function onUnauthorized(handler) {
  unauthorizedHandler = handler
}

function authHeaders() {
  const token = localStorage.getItem(TOKEN_KEY)
  return token ? { Authorization: `Bearer ${token}` } : {}
}

// 一次 5xx 或断网不该让用户「莫名其妙退出登录」。会话恢复要能分清
// 「令牌真的无效（401/403）」和「这次请求恰好失败」，所以错误对象必须带上状态码。
export class ApiError extends Error {
  constructor(message, status, payload) {
    super(message)
    this.name = 'ApiError'
    this.status = status
    this.payload = payload
  }
}

export function isAuthError(error) {
  return error instanceof ApiError && (error.status === 401 || error.status === 403)
}

/**
 * 把一次失败的响应说成人话。
 *
 * **绝不能把 `JSON.parse` 的异常抛给用户**。后端 500 时 Starlette 回的是
 * `text/plain` 的 `Internal Server Error`；Vite 代理在后端重启的那几秒回 500
 * 也是纯文本。以前调用方自己 `JSON.parse(text)`，于是用户看到的报错是
 * `Unexpected token 'I', "Internal S"... is not valid JSON` —— 既不说明发生了什么，
 * 也无法判断该不该重试（而这恰恰是唯一有用的信息）。
 *
 * 现在的顺序：优先用后端给的 `detail`（那是**我们**写的、可执行的说明），
 * 其次认出「非 JSON 的 5xx」= 服务端/代理挂了这类可重试的情况，最后才退回状态码。
 */
function describeFailure(response, payload, text) {
  const detail = payload?.detail
  const problems = typeof detail === 'object' ? detail.problems || detail.failures : detail
  if (Array.isArray(problems) && problems.length) return problems.join('；')
  if (typeof problems === 'string' && problems) return problems

  // 网关 / 代理 / 应用崩溃：body 不是 JSON，且状态码是 5xx。
  // 这类失败**几乎都是暂态**（后端重启、灌库期间连接被断），值得明确说「可重试」。
  if (response.status >= 500) {
    const snippet = (text || '').trim().slice(0, 120)
    return (
      `服务端暂时不可用（HTTP ${response.status}）` +
      (snippet && !snippet.startsWith('<') ? `：${snippet}` : '') +
      '。这通常是后端正在重启或灌库，稍等几秒重试即可'
    )
  }
  return `HTTP ${response.status}${response.statusText ? ` ${response.statusText}` : ''}`
}

/**
 * 底层请求。**导出是为了让管理页复用它**，而不是各自再写一份
 * `fetch` + `JSON.parse`：重复实现的那一份漏了「非 JSON 的失败响应」处理，
 * 于是后端 500 时用户看到的是 `Unexpected token 'I'...`（真实事故，见 describeFailure）。
 */
export async function request(path, options = {}) {
  let response
  try {
    response = await fetch(`${BASE}${path}`, {
      ...options,
      headers: {
        // 只有**字符串** body 才是我们自己序列化的 JSON；FormData（名单 CSV 上传）
        // 的 Content-Type 必须由浏览器带着 boundary 自己填，写死成 JSON 会让后端 422。
        ...(typeof options.body === 'string' ? { 'Content-Type': 'application/json' } : {}),
        ...authHeaders(),
        ...(options.headers || {}),
      },
    })
  } catch (cause) {
    throw new ApiError('网络请求失败，请检查连接后重试', 0, { cause })
  }
  const text = await response.text()
  let payload = null
  try {
    payload = text ? JSON.parse(text) : null
  } catch {
    payload = null
  }
  if (response.status === 401 && localStorage.getItem(TOKEN_KEY) && unauthorizedHandler) {
    unauthorizedHandler()
  }
  if (!response.ok) {
    throw new ApiError(describeFailure(response, payload, text), response.status, payload)
  }
  return payload
}

function qs(params) {
  const query = new URLSearchParams()
  for (const [key, value] of Object.entries(params)) {
    if (value === undefined || value === null || value === '' || value === false) continue
    query.set(key, String(value))
  }
  const text = query.toString()
  return text ? `?${text}` : ''
}

export const api = {
  health: () => request('/health'),
  stats: () => request('/stats'),
  sources: () => request('/stats/sources'),
  tracks: () => request('/tracks'),
  regions: () => request('/regions'),
  clusters: (params = {}) => request(`/clusters${qs(params)}`),
  cluster: (slug) => request(`/clusters/${encodeURIComponent(slug)}`),
  enterprises: (params = {}) => request(`/enterprises${qs(params)}`),
  enterprise: (id) => request(`/enterprises/${id}`),

  // 账号与收藏
  register: (payload) => request('/account/register', { method: 'POST', body: JSON.stringify(payload) }),
  login: (payload) => request('/account/login', { method: 'POST', body: JSON.stringify(payload) }),
  me: () => request('/account/me'),
  favorites: () => request('/account/favorites'),
  favoriteIds: () => request('/account/favorites/ids'),
  addFavorite: (enterpriseId, note = '') =>
    request('/account/favorites', {
      method: 'POST',
      body: JSON.stringify({ enterprise_id: enterpriseId, note }),
    }),
  removeFavorite: (enterpriseId) =>
    request(`/account/favorites/${enterpriseId}`, { method: 'DELETE' }),
  purgeStaleFavorites: () => request('/account/favorites/unresolved', { method: 'DELETE' }),
  updateFavoriteNote: (enterpriseId, note) =>
    request(`/account/favorites/${enterpriseId}`, {
      method: 'PATCH',
      body: JSON.stringify({ note }),
    }),
}

export const GRADE_CLASS = {
  official: 'grade-official',
  gov_open: 'grade-gov',
  media: 'grade-media',
  site_curated: 'grade-curated',
  self_reported: 'grade-self',
}

// 来源名单内的核实档位（「A 官网公示」这种长文案也可能只有字母）。
// 只有字母时要展开 —— 「核实档位：B」对读者等于没说。
const TIER_MEANING = {
  A: 'A 官网公示',
  B: 'B 工商可核',
  C: 'C 线索（未核）',
  D: 'D 其他',
  '—': '未标档位',
}

export function tierLabel(tier) {
  const value = (tier || '').trim()
  if (!value) return ''
  if (TIER_MEANING[value]) return TIER_MEANING[value]
  // 已经带中文说明（「A 官网公示」）就原样返回
  return /[\u4e00-\u9fa5]/.test(value) ? value : `${value} 档`
}

// 与后端 schemas.GRADE_LABELS 一一对应。前端需要按荣誉/来源各自的
// 核实程度渲染标签（同一家企业的不同来源可以不同档），所以这里要有一份。
export const GRADE_LABELS = {
  official: '官方认定',
  gov_open: '政府公开信息',
  media: '媒体 · 协会来源',
  site_curated: '站方整理',
  self_reported: '企业自报 · 本站未核验',
}
// 「这个链接不是本企业自己的站」—— 同一个集团多家子公司共用一个域名很常见
// （三家安费诺都指向 amphenol.com），不说明就会让人以为是同一个站。
export const PARENT_SITE_KINDS = new Set(['集团官网', '母站企业官网', '母公司官网'])

// 站方对这家企业做过什么的记录。这是**我们**的工作状态，
// 不是数据本身的核实程度（那是 data_grade / contact_sources），所以要写清楚归属。
// 用「核验」而不是「抓取」：读者不需要知道实现细节，
// 而「抓取」只会让人以为这家企业被我们审查过。
export const ENRICH_LABELS = {
  pending: '尚未核验',
  verified: '已核实到官网',
  no_site_found: '未找到本企业独立官网',
  no_candidate: '无可尝试的候选域名',
  failed_revalidation: '复验未通过，已撤下官网',
  error: '核验出错',
}
