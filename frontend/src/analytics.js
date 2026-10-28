/**
 * Google Analytics 4 的页面浏览上报。
 *
 * 为什么单独一个模块，而不是把 gtag 调用散在各视图里：
 * 这是一个 SPA。GA4 的自动 page_view 只在**首次**加载时发一次，站内跳转
 * （`/clusters` → `/enterprises`）它看不到 —— 不补这一层，报表里永远只有
 * 落地页，看不出用户实际在库里翻到哪一步。
 *
 * 加载本身在 `index.html` 里完成（要尽早发出请求，且要在 JS 包下载之前就
 * 判断该不该加载）。这里只负责「什么时候、报哪个页面」，不重复加载脚本。
 *
 * 三条底线（对应 index.html 那段注释，两处要一起看）：
 *   1. gtag 不存在时静默跳过。统计是旁路：加载失败、被拦截插件挡掉、
 *      本地开发没加载，都不该让页面报错。
 *   2. 不发送任何账号标识。隐私条款承诺「账号信息不与第三方共享」，
 *      所以这里没有 `user_id`，也没有自定义维度 —— 一旦加上，
 *      报表就能把浏览行为关联到具体某个人，承诺也就不成立了。
 *   3. 管理页面不上报。`/admin/*` 是站方自己的维护入口，混进来会把
 *      「公众访问量」这个口径污染掉，而这些页面也不是给读者看的。
 */

/** 维护入口：不计入公众访问统计（见文件头第 3 条）。 */
const EXCLUDED_PREFIXES = ['/admin']

function gtag(...args) {
  if (typeof window.gtag !== 'function') return
  window.gtag(...args)
}

/**
 * 报一次 page_view。
 *
 * 必须在 `document.title` 已经写成新页面的标题之后调用 —— 否则
 * 报表里的 page_title 会是上一页的（路由切换时标题总是后写的）。
 *
 * @param {{ fullPath: string, path: string }} route vue-router 的路由对象
 */
export function trackPageView(route) {
  if (!route) return
  const path = route.path || ''
  if (EXCLUDED_PREFIXES.some((prefix) => path === prefix || path.startsWith(`${prefix}/`))) return

  gtag('event', 'page_view', {
    page_title: document.title,
    page_location: window.location.href,
    // 用 fullPath 而不是 location.href：带查询参数（`?q=`、`?page=`）
    // 才能看出读者是怎么翻库的；hash 不进 path，避免同一页面被拆成多条。
    page_path: route.fullPath,
  })
}
