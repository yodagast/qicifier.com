/** 登录态：令牌存 localStorage，刷新后自动恢复；对外暴露响应式 user。 */
import { computed, reactive } from 'vue'
import { api, isAuthError, onUnauthorized } from './api'

const TOKEN_KEY = 'zanzhu.token'

export const session = reactive({
  token: localStorage.getItem(TOKEN_KEY) || '',
  user: null,
  ready: false,
  // 令牌失效与「从没登录过」是两件事：前者要告诉用户「登录已过期」，
  // 否则他会以为自己记错了密码。
  expired: false,
  favoriteIds: new Set(),
})

// 任何一次带令牌的请求收到 401，都说明这个令牌不能用了 —— 立刻把本地登录态收掉，
// 否则页面会一直显示「已登录」，点什么都报错。
onUnauthorized(() => {
  if (!session.token) return
  setToken('')
  session.user = null
  session.favoriteIds = new Set()
  session.expired = true
})

export const isLoggedIn = computed(() => Boolean(session.user))

function setToken(token) {
  session.token = token || ''
  if (token) localStorage.setItem(TOKEN_KEY, token)
  else localStorage.removeItem(TOKEN_KEY)
}

export async function restore() {
  session.expired = false
  if (!session.token) {
    session.ready = true
    return
  }
  try {
    session.user = await api.me()
  } catch (error) {
    // 只有令牌真的无效时才登出。一次 500 或断网就把人踢下线，
    // 用户会以为是「登录系统坏了」，而其实什么都没错。
    if (isAuthError(error)) {
      setToken('')
      session.expired = true
    }
    session.user = null
    session.ready = true
    return
  }
  // 收藏 id 拉失败不影响登录态，让它自己重试（列表页会再拉一次）
  try {
    const { ids } = await api.favoriteIds()
    session.favoriteIds = new Set(ids)
  } catch {
    /* 保持已登录，收藏标记留空 */
  }
  session.ready = true
}

export async function register(payload) {
  const data = await api.register(payload)
  setToken(data.token)
  session.user = data.user
  session.favoriteIds = new Set()
  // 登录成功后必须清掉「已过期」标记：否则用户重新登录成功、
  // 再次进入 /login 时仍会看到「登录已过期」的红字。
  session.expired = false
  return data.user
}

export async function login(payload) {
  const data = await api.login(payload)
  setToken(data.token)
  session.user = data.user
  session.expired = false
  // 收藏 id 只是页面上的星标状态，拉不到不该让「登录」这一步失败。
  // 以前这里直接 await，收藏接口一次抖动就会抛错：用户明明已经登录成功
  // （令牌已写入），页面却弹出「网络请求失败」并停在登录页 ——
  // 再点一次登录才「忽然好了」。与 restore() 的处理保持一致。
  try {
    const { ids } = await api.favoriteIds()
    session.favoriteIds = new Set(ids)
  } catch {
    session.favoriteIds = new Set()
  }
  return data.user
}

export function logout() {
  setToken('')
  session.user = null
  session.favoriteIds = new Set()
}

export function isFavorited(enterpriseId) {
  return session.favoriteIds.has(Number(enterpriseId))
}

export async function toggleFavorite(enterpriseId, note = '') {
  const id = Number(enterpriseId)
  if (session.favoriteIds.has(id)) {
    await api.removeFavorite(id)
    session.favoriteIds.delete(id)
    session.favoriteIds = new Set(session.favoriteIds)
    if (session.user) session.user.favorite_count = Math.max(0, session.user.favorite_count - 1)
    return false
  }
  await api.addFavorite(id, note)
  session.favoriteIds.add(id)
  session.favoriteIds = new Set(session.favoriteIds)
  if (session.user) session.user.favorite_count += 1
  return true
}