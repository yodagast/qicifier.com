<script setup>
import { ref } from 'vue'
import { useRouter } from 'vue-router'
import { isLoggedIn, logout, session } from './session'
import {
  COMPANY_NAME,
  CONTACT_EMAIL,
  COPYRIGHT_YEAR,
  GONGAN_ICON,
  GONGAN_NO,
  GONGAN_URL,
  ICP_NO,
  ICP_URL,
} from './site'

const router = useRouter()
const keyword = ref('')

function signOut() {
  logout()
  router.push({ name: 'home' })
}

function submit() {
  const q = keyword.value.trim()
  router.push({ name: 'enterprises', query: q ? { q } : {} })
}
</script>

<template>
  <header class="site-header">
    <div class="container inner">
      <RouterLink class="brand" to="/">浙江产业带<span>企业库</span></RouterLink>
      <!-- 顶部导航只留三个内容入口 + 关于我们。
           「数据来源」是说明性页面，已移到页脚的「关于我们」列（与「关于本站」同组）。 -->
      <nav class="nav">
        <RouterLink to="/map">产业地图</RouterLink>
        <RouterLink to="/clusters">产业集群</RouterLink>
        <RouterLink to="/enterprises">企业名录</RouterLink>
        <RouterLink to="/company">关于我们</RouterLink>
      </nav>
      <form class="header-search" @submit.prevent="submit">
        <input v-model="keyword" type="search" placeholder="企业名 / 产品" aria-label="检索企业" />
        <button class="primary" type="submit">检索</button>
      </form>
      <div class="account">
        <template v-if="isLoggedIn">
          <RouterLink class="account-link" to="/favorites" title="我的收藏">
            <span class="star">★</span> 收藏 {{ session.user?.favorite_count ?? 0 }}
          </RouterLink>
          <span class="account-name tiny muted">{{ session.user?.display_name }}</span>
          <button class="tiny-btn" @click="signOut">退出</button>
        </template>
        <template v-else>
          <RouterLink class="account-link" to="/login">登录</RouterLink>
          <RouterLink class="account-link primary-link" :to="{ name: 'login', query: { mode: 'register' } }">
            注册
          </RouterLink>
        </template>
      </div>
    </div>
  </header>

  <main>
    <RouterView />
  </main>

  <footer class="site-footer">
    <div class="container">
      <!-- 三列结构：客户服务 / 网站地图 / 关于我们。
           五条内容约定，改页脚时不要回退：
             1. 网站地图列只放**通用入口**，不列具体类目 —— 类目随运营增删，
                写死在页脚必然过期；
             2. 账户流（登录 / 收藏）不进页脚：它们属于登录后的账户中心，
                未登录点进去只会被踢回登录框，放在页脚等于制造无效点击；
             3. 不展示支付方式与任何价格：本站不下单、不收款、不撮合；
             4. 本站**只公开一个联系邮箱**，不列客服电话，也不列个人联系方式；
             5. 说明性页面归「关于我们」列（关于本站 / 数据导入 / 数据来源与核实程度）。
                客户服务列只放「服务」类入口（隐私条款 / 联系邮箱），
                不放说明页 —— 两列的分工是「怎么办事」与「站是什么」。
                三者的顺序是刻意的：关于本站（站是什么）→ 数据导入（数据怎么进来的）
                → 数据来源与核实程度（进来的数据有多可信），别打乱。 -->
      <div class="footer-cols">
        <div class="footer-col">
          <h4>客户服务</h4>
          <ul>
            <li><RouterLink :to="{ name: 'service', hash: '#privacy' }">隐私条款</RouterLink></li>
            <li>
              联系邮箱
              <a class="footer-mail" :href="`mailto:${CONTACT_EMAIL}`" :title="`发送邮件至 ${CONTACT_EMAIL}`">
                {{ CONTACT_EMAIL }}
              </a>
            </li>
          </ul>
        </div>

        <div class="footer-col">
          <h4>网站地图</h4>
          <ul>
            <li><RouterLink :to="{ name: 'home' }">首页</RouterLink></li>
            <li><RouterLink :to="{ name: 'map' }">产业地图</RouterLink></li>
            <li><RouterLink :to="{ name: 'clusters' }">产业集群</RouterLink></li>
            <li><RouterLink :to="{ name: 'enterprises' }">企业名录</RouterLink></li>
            <li><RouterLink :to="{ name: 'company' }">关于我们</RouterLink></li>
          </ul>
        </div>

        <div class="footer-col">
          <h4>关于我们</h4>
          <ul>
            <li><RouterLink :to="{ name: 'company', hash: '#about-us' }">关于本站</RouterLink></li>
            <!-- 「数据来源」与「关于本站」同属说明性页面，所以放在同一列；
                 顶部导航因此不再重复它（2026-10-15 调整）。 -->
            <!-- 「数据导入」夹在两者之间：它讲的是**数据怎么进库的**，正好是
                 「站是什么」往「数据可不可信」过渡的那一格。指向 /admin/contacts
                 的批量导入段落（导入入口本来就在那里，这里只是给它一个能直达的地址，
                 不是新开一个页面）。 -->
            <li><RouterLink :to="{ name: 'admin-contacts', hash: '#import' }">数据导入</RouterLink></li>
            <li><RouterLink :to="{ name: 'about' }">数据来源与核实程度</RouterLink></li>
          </ul>
        </div>
      </div>

      <!--
        备案信息栏（工信部 + 公安部法定要求），该行为法定强制展示内容：
        1. ICP 备案号必须展示，且必须可点击跳转至工信部备案系统 beian.miit.gov.cn；
        2. 必须同时展示网站主办单位名称（主体全称）；
        3. 公安联网备案号必须与官方盾牌图标一起展示，且链接到公安部
           「全国互联网安全管理服务平台」查询页（带 code 参数）。
        三者缺一即不合规，不要为了排版好看省掉任何一项。
        主体名与备案号统一在 src/site.js 定义，不要在这里硬编码。

        注：原先页脚这里还有两段「收录纪律 / 退出边界」声明，已整理到 /company 页。
        主办单位名后**不再**留入口链接（法定备案栏只放备案要素）。
        这一段仍可在页脚「关于我们」列点「关于本站」到达 ——
        那里是内容入口，不是法定展示位。
      -->
      <div class="footer-beian">
        <span class="beian-org">© {{ COPYRIGHT_YEAR }} {{ COMPANY_NAME }} 版权所有</span>
        <a
          class="beian-link"
          :href="ICP_URL"
          target="_blank"
          rel="noopener noreferrer"
          title="工业和信息化部政务服务平台"
        >
          <svg
            class="beian-shield"
            viewBox="0 0 24 24"
            width="12"
            height="12"
            aria-hidden="true"
            focusable="false"
          >
            <path
              d="M12 2 4 5v6.2c0 5 3.4 9.3 8 10.8 4.6-1.5 8-5.8 8-10.8V5l-8-3Zm0 3.2 5 1.9v4.3c0 3.4-2.1 6.4-5 7.5-2.9-1.1-5-4.1-5-7.5V7.1l5-1.9Z"
              fill="currentColor"
            />
          </svg>
          {{ ICP_NO }}
        </a>
        <a
          class="beian-link gongan-link"
          :href="GONGAN_URL"
          target="_blank"
          rel="noreferrer noopener"
          title="全国互联网安全管理服务平台"
        >
          <img
            class="gongan-icon"
            :src="GONGAN_ICON"
            alt="公安备案图标"
            width="18"
            height="20"
            loading="lazy"
          />
          {{ GONGAN_NO }}
        </a>
      </div>
    </div>
  </footer>
</template>

<style scoped>
.site-footer {
  margin-top: 40px;
  border-top: 1px solid var(--rule-strong);
  background: var(--panel);
}

.footer-cols {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 28px;
  padding: 28px 0 22px;
}

.footer-col h4 {
  font-size: var(--fs-sm);
  margin: 0 0 10px;
  font-weight: 700;
}

.footer-col ul {
  list-style: none;
  margin: 0;
  padding: 0;
  display: grid;
  gap: 7px;
}

.footer-col li {
  font-size: 13px;
  color: var(--muted);
}

.footer-col li a:hover {
  color: var(--brand);
}

.footer-mail {
  color: var(--text);
  word-break: break-all;
}

.footer-mail:hover {
  color: var(--brand);
}

/* 备案信息栏：法定强制展示内容，不参与响应式折叠，窄屏改为竖排也不省略。 */
.footer-beian {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  justify-content: center;
  gap: 6px 18px;
  padding: 14px 0;
  font-size: 12px;
  color: var(--muted);
}

.footer-beian a {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  color: var(--muted);
}

.footer-beian a:hover {
  color: var(--brand);
}

/* 备案号与图标之间不能换行断开，否则图标会漂到下一行单独出现。 */
.beian-org,
.beian-link {
  white-space: nowrap;
}

.beian-shield {
  flex: none;
}

.gongan-icon {
  flex: none;
  display: block;
}

/* 三列都短（最长不过是「数据来源与核实程度」和一个邮箱），所以不需要 900px 就折成两列：
   在 700px 以上维持三列反而是最省纵向空间的排法。 */
@media (max-width: 700px) {
  .footer-cols {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}

@media (max-width: 480px) {
  .footer-cols {
    grid-template-columns: minmax(0, 1fr);
  }
}
</style>