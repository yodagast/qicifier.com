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

async function request(path, options = {}) {
  let response
  try {
    response = await fetch(`${BASE}${path}`, {
      ...options,
      headers: {
        ...(options.body ? { 'Content-Type': 'application/json' } : {}),
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
    const detail = payload?.detail
    const problems = typeof detail === 'object' ? detail.problems || detail.failures : detail
    const message = Array.isArray(problems) ? problems.join('；') : problems || `${response.status}`
    throw new ApiError(message, response.status, payload)
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
