import { createApp } from 'vue'
import { createRouter, createWebHistory } from 'vue-router'
import App from './App.vue'
import Home from './views/Home.vue'
import MapView from './views/MapView.vue'
import ClusterList from './views/ClusterList.vue'
import ClusterDetail from './views/ClusterDetail.vue'
import EnterpriseList from './views/EnterpriseList.vue'
import EnterpriseDetail from './views/EnterpriseDetail.vue'
import About from './views/About.vue'
import CustomerService from './views/CustomerService.vue'
import Company from './views/Company.vue'
import Admin from './views/Admin.vue'
import AdminClusters from './views/AdminClusters.vue'
import Login from './views/Login.vue'
import Favorites from './views/Favorites.vue'
import { restore } from './session'
import { trackPageView } from './analytics'
import './styles.css'

// 浏览器标签页标题的后缀（「首页 · 杭州波动粒子」）。
// 与 index.html 里的静态 <title> 必须一致 —— 那份是 JS 执行前的兜底，
// 不一致会让首屏闪一下旧名字，搜索结果里也会出现两个站名。
const SITE_TITLE = '杭州波动粒子'

const routes = [
  { path: '/', name: 'home', component: Home, meta: { title: '首页' } },
  { path: '/map', name: 'map', component: MapView, meta: { title: '产业地图' } },
  { path: '/clusters', name: 'clusters', component: ClusterList, meta: { title: '产业集群' } },
  { path: '/clusters/:slug', name: 'cluster', component: ClusterDetail, props: true, meta: { title: '集群档案' } },
  { path: '/enterprises', name: 'enterprises', component: EnterpriseList, meta: { title: '企业名录' } },
  { path: '/enterprises/:id', name: 'enterprise', component: EnterpriseDetail, props: true, meta: { title: '企业详情' } },
  { path: '/about', name: 'about', component: About, meta: { title: '数据来源与核实程度' } },
  { path: '/service', name: 'service', component: CustomerService, meta: { title: '客户服务' } },
  { path: '/company', name: 'company', component: Company, meta: { title: '关于我们' } },
  { path: '/admin/contacts', name: 'admin-contacts', component: Admin, meta: { title: '人工补充联系方式' } },
  { path: '/admin/clusters', name: 'admin-clusters', component: AdminClusters, meta: { title: '产业带档案编辑' } },
  { path: '/login', name: 'login', component: Login, meta: { title: '登录 / 注册' } },
  { path: '/favorites', name: 'favorites', component: Favorites, meta: { title: '我的收藏' } },
]

const router = createRouter({
  history: createWebHistory(),
  routes,
  // 站内分节链接（页脚与各页之间的 #privacy / #policy / #about-us 等）必须真的滚过去。
  // 原先是 `() => ({ top: 0 })`，于是点「收录纪律与退出边界」只改 URL 不滚动 ——
  // 用户以为链接坏了。hash 优先，没有 hash 才回顶部；用 smooth 让跳转可被察觉。
  scrollBehavior: (to, from, savedPosition) => {
    if (savedPosition) return savedPosition
    if (to.hash) {
      // 目标元素不在 DOM 里时**不要**把选择器交给 router：它会打印
      // "Couldn't find element using selector ... returned by scrollBehavior"，
      // 而真实原因通常是「这个区块要等权限/数据渲染出来」（例：/admin/contacts#import
      // 在解锁后才出现）。那种情况由页面自己在渲染完成后补滚一次，见 Admin.vue
      // 的 scrollToImportIfAsked。这里返回 false 表示「本轮不滚」。
      let present = false
      try {
        present = !!document.querySelector(to.hash)
      } catch {
        present = false // hash 不是合法选择器（极少见）：按「不存在」处理
      }
      if (!present) return false
      return { el: to.hash, behavior: 'smooth', top: 76 }
    }
    return { top: 0 }
  },
})

router.afterEach((to) => {
  const title = to.meta?.title ? `${to.meta.title} · ${SITE_TITLE}` : SITE_TITLE
  document.title = title
  // 必须在写 title 之后调用：page_title 取的就是上面刚落定的值，
  // 顺序反了，报表里每个页面都会顶着上一个页面的标题。
  // 首屏也走这里（首次导航同样触发 afterEach），所以不需要另发一条。
  trackPageView(to)
})

// 恢复登录态（令牌在 localStorage，刷新后自动带回用户与收藏列表）
restore()

createApp(App).use(router).mount('#app')