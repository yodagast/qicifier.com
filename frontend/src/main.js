import { createApp } from 'vue'
import { createRouter, createWebHistory } from 'vue-router'
import App from './App.vue'
import { restore } from './session'
import { applySeo } from './seo'
import { trackPageView } from './analytics'
import './styles.css'

// 视图一律懒加载。
//
// 之前 13 个视图全是静态 import，连 Admin.vue（737 行）、AdminClusters.vue、
// Login.vue 都打进首屏那个包 —— 访客根本用不到。拆开后首屏只留首页/列表/详情
// 这几条主路径：主包实测 210.8KB → 118.7KB（gzip 47.7KB）。
const Home = () => import('./views/Home.vue')
const MapView = () => import('./views/MapView.vue')
const ClusterList = () => import('./views/ClusterList.vue')
const ClusterDetail = () => import('./views/ClusterDetail.vue')
const EnterpriseList = () => import('./views/EnterpriseList.vue')
const EnterpriseDetail = () => import('./views/EnterpriseDetail.vue')
const About = () => import('./views/About.vue')
const ResearchList = () => import('./views/ResearchList.vue')
const ResearchArticle = () => import('./views/ResearchArticle.vue')
const CustomerService = () => import('./views/CustomerService.vue')
const Company = () => import('./views/Company.vue')
const Admin = () => import('./views/Admin.vue')
const AdminClusters = () => import('./views/AdminClusters.vue')
const Login = () => import('./views/Login.vue')
const Favorites = () => import('./views/Favorites.vue')

// 每页的 title 与 description。
//
// description 写的是**这一页能回答什么**，不是关键词堆 —— 搜索结果的摘要由它生成，
// AI 引擎也优先抽它。详情页（集群 / 企业）的 title 与 description 由页面取到数据后
// 用 seo.js 的 applySeo 覆盖，这里的值只是「数据还没到」时的兜底。
//
// noindex: true 的是账户页与后台：它们没有检索价值，被索引只会稀释整站质量。
const routes = [
  {
    path: '/',
    name: 'home',
    component: Home,
    meta: {
      title: '首页',
      seo: {
        description:
          '浙江 11 个设区市、90 个县（市、区）的产业集群与企业名录：以官方名单为骨架、逐条标注核实程度，415X 赛道与镇街级块状经济分档收录，可按产品、集群、镇街与核实程度检索。',
      },
    },
  },
  {
    path: '/map',
    name: 'map',
    component: MapView,
    meta: {
      title: '产业地图',
      seo: {
        description:
          '浙江 11 个设区市 · 90 个县级行政区，有产业的镇/街道单独成一级实体。点选行政区或镇街，查看落在该地的产业集群档案。',
      },
    },
  },
  {
    path: '/clusters',
    name: 'clusters',
    component: ClusterList,
    meta: {
      title: '产业集群',
      seo: {
        description:
          'A 级为有官方认定的 415X 赛道，C 级为无官方名单的镇街级块状经济。逐条标注官方认定 / 政府公开 / 媒体·协会 / 站方整理，绝不混档。',
      },
    },
  },
  {
    path: '/clusters/:slug',
    name: 'cluster',
    component: ClusterDetail,
    props: true,
    meta: {
      title: '集群档案',
      seo: {
        description: '集群档案：主产区、认定与荣誉、规模指标、代表企业名单，每个数字都带来源与核验日期。',
      },
    },
  },
  {
    path: '/enterprises',
    name: 'enterprises',
    component: EnterpriseList,
    meta: {
      title: '企业名录',
      seo: {
        description:
          '按产品、集群、镇街、核实程度检索浙江企业名录。排序只依据公开可核验程度与资料完整度，对所有企业一致；不收入驻费、不卖排名。',
      },
    },
  },
  {
    path: '/enterprises/:id',
    name: 'enterprise',
    component: EnterpriseDetail,
    props: true,
    meta: {
      title: '企业详情',
      seo: {
        description: '企业详情：所属产业集群、主营产品、公开联系方式，以及每一条信息的来源与核实程度。',
      },
    },
  },
  {
    path: '/research',
    name: 'research',
    component: ResearchList,
    meta: {
      title: '行业研究',
      seo: {
        description:
          '站方自己写的方法论、调研报告与数据口径说明：415X 赛道怎么认定、镇街级块状经济为什么没有官方名单、五档核实程度怎么判。',
      },
    },
  },
  {
    path: '/research/:slug',
    name: 'research-article',
    component: ResearchArticle,
    props: true,
    meta: {
      title: '行业研究',
      seo: {
        description: '行业研究文章：方法论、调研报告与数据口径说明。',
      },
    },
  },
  {
    path: '/about',
    name: 'about',
    component: About,
    meta: {
      title: '数据来源与核实程度',
      seo: {
        description:
          '五级核实档位（官方认定 / 政府公开 / 媒体·协会 / 站方整理 / 企业自报）的判定口径，以及本站收录的每一份名单的来源披露。',
      },
    },
  },
  {
    path: '/service',
    name: 'service',
    component: CustomerService,
    meta: {
      title: '客户服务',
      seo: {
        description: '客户服务说明：隐私条款与联系邮箱。本站是公开信息整理工具，不参与交易、不撮合、不收款。',
      },
    },
  },
  {
    path: '/company',
    name: 'company',
    component: Company,
    meta: {
      title: '关于我们',
      seo: {
        description: '主办单位、备案信息，以及本站的收录纪律与退出边界：不收入驻费、不卖排名、不做付费认证与等级背书。',
      },
    },
  },
  {
    path: '/admin/contacts',
    name: 'admin-contacts',
    component: Admin,
    meta: { title: '人工补充联系方式', seo: { noindex: true } },
  },
  {
    path: '/admin/clusters',
    name: 'admin-clusters',
    component: AdminClusters,
    meta: { title: '产业带档案编辑', seo: { noindex: true } },
  },
  {
    path: '/login',
    name: 'login',
    component: Login,
    meta: { title: '登录 / 注册', seo: { noindex: true } },
  },
  {
    path: '/favorites',
    name: 'favorites',
    component: Favorites,
    meta: { title: '我的收藏', seo: { noindex: true } },
  },
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
  const seo = to.meta?.seo || {}
  applySeo({
    title: to.meta?.title,
    description: seo.description || '',
    // 用 to.path 而不是 to.fullPath：``?q=`` / ``?page=`` 会造出无限 URL 空间，
    // robots 已经挡掉它们，canonical 自然也不能指向它们。
    path: to.path,
    noindex: seo.noindex === true,
  })
  // 必须在写 title 之后调用：page_title 取的就是上面刚落定的值，
  // 顺序反了，报表里每个页面都会顶着上一个页面的标题。
  // 首屏也走这里（首次导航同样触发 afterEach），所以不需要另发一条。
  trackPageView(to)
})

// 恢复登录态（令牌在 localStorage，刷新后自动带回用户与收藏列表）
restore()

createApp(App).use(router).mount('#app')