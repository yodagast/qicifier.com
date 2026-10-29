<script setup>
import {
  COMPANY_NAME,
  CONTACT_EMAIL,
  COPYRIGHT_YEAR,
  GONGAN_ICON,
  GONGAN_NO,
  GONGAN_URL,
  ICP_NO,
  ICP_URL,
  SERVICES,
} from '../site'

// 「关于我们」页有两块内容：主办单位信息（备案 + 联系邮箱 + 版权）与
// 「收录纪律与退出边界」。
//
// 三条内容约定：
//   1. 「主办单位」一栏必须与备案系统登记全称逐字一致，因此从 src/site.js 取，
//      不在这里硬编码；
//   2. 联系方式只有 contact 邮箱一个，不列客服电话与任何个人联系方式；
//   3. 「收录纪律与退出边界」那一节原先是页脚底部的两段声明，整理过来时只做了
//      分组与顺序调整，**每一条断言的语义都不改** —— 它是本站对外承诺，
//      措辞改了就是另一回事。「不含任何个人联系方式」必须与 /service 的隐私条款、
//      /about 的账号与隐私同口径。
//
// 曾有过「用户故事」「公司故事」「公司新闻」三节，均已移除：本站不做客户关系维护、
// 不运营内容栏目，写这些等于给页面加上无法长期维护的承诺，也不属于备案要求展示的内容。
//
// 2026-10-26 新增「我们提供的服务」一节（主办单位对外的四项业务）。它与上面那份
// 声明是**两件事**，所以两处必须互相指认，否则读者会以为「不卖排名」是句空话：
//   * 收录与核实免费且不可购买（本页「收录纪律与退出边界」一节）；
//   * 这四项是主办单位另设的对外业务，不影响是否收录、也不影响排序位置。
// 措辞按主办单位对外的原文写，名称本身不改写 —— 见 site.js 的 SERVICES。
</script>

<template>
  <div class="container">
    <div id="about-us" class="page-head">
      <h1>关于我们</h1>

      <div class="card panel">
        <h3>主办单位</h3>
        <p class="small muted">
          本站由 <strong>{{ COMPANY_NAME }}</strong> 主办并运营，备案信息与工信部、公安部备案系统登记内容一致。
        </p>
        <ul class="beian-inline">
          <li>
            <span class="muted small">ICP 备案</span>
            <a :href="ICP_URL" target="_blank" rel="noopener noreferrer">{{ ICP_NO }}</a>
          </li>
          <li>
            <span class="muted small">公安备案</span>
            <a :href="GONGAN_URL" target="_blank" rel="noreferrer noopener">
              <img :src="GONGAN_ICON" alt="公安备案图标" width="18" height="20" loading="lazy" />
              {{ GONGAN_NO }}
            </a>
          </li>
          <li>
            <span class="muted small">联系邮箱</span>
            <a :href="`mailto:${CONTACT_EMAIL}`" :title="`发送邮件至 ${CONTACT_EMAIL}`">
              {{ CONTACT_EMAIL }}
            </a>
          </li>
          <li>
            <span class="muted small">版权</span>
            <span>© {{ COPYRIGHT_YEAR }} {{ COMPANY_NAME }} 版权所有</span>
          </li>
        </ul>
      </div>
    </div>

    <!--
      「我们提供的服务」放在备案区块与收录纪律之间，是有顺序理由的：
      先交代「谁在办这个站」（主办单位），再交代「主办单位还做什么」（本节），
      最后交代「这个站自己守什么规矩」（收录纪律）。反过来放，读者会先看到
      四项业务，再看到「不卖排名」，两段的关联就得自己猜。
    -->

    <section id="services" class="section">
      <div class="card panel">
        <h3>我们提供的服务</h3>
        <p class="small muted">
          {{ COMPANY_NAME }} 面向制造业企业提供的四项对外服务：
        </p>
        <ul class="service-list">
          <li v-for="service in SERVICES" :key="service">{{ service }}</li>
        </ul>
        <p class="small muted">
          <strong>这些服务与本站的收录、排序无关。</strong>
          本站是否收录一家企业、按什么顺序展示，只依据公开可核验程度与资料完整度，
          对所有企业一致 —— 不收入驻费、不卖排名、不接受竞价、不做付费认证与等级背书
          （见下节
          <a href="#policy">收录纪律与退出边界</a>
          ）。上述服务也不通过本站站内功能提供，
          如需了解请
          <a :href="`mailto:${CONTACT_EMAIL}`" :title="`发送邮件至 ${CONTACT_EMAIL}`">
            {{ CONTACT_EMAIL }}
          </a>
          。
        </p>
      </div>
    </section>

    <!--
      本节原先是页脚底部两段声明（「以官方名单为骨架…」与「只展示公开地址…」），
      按站方要求整理到这里。整理时只做分组与顺序调整，**每一条断言的语义都不改**：

      * 原文第一段拆成「收录与排序」「付费边界」两组 —— 前者说信息怎么来的，
        后者说钱不能改变什么，混在一段里读者会只看后半句；
      * 原文第二段拆成「信息公开边界」「交易边界」两组，同理；
      * 「不含任何个人联系方式」与 /service 的隐私条款、/about 的账号与隐私
        必须完全同口径，改一处就要三处一起看。
    -->
    <section id="policy" class="section">
      <div class="card panel">
        <h3>收录纪律与退出边界</h3>

        <h4>收录与排序</h4>
        <ul class="policy-list">
          <li>以官方名单为骨架，逐条标注核实程度。</li>
          <li>排序只依据公开可核验程度与资料完整度，对所有企业一致。</li>
        </ul>

        <h4>付费边界</h4>
        <ul class="policy-list">
          <li>不收入驻费、不卖排名、不接受竞价、不做付费认证。</li>
        </ul>

        <h4>信息公开边界</h4>
        <ul class="policy-list">
          <li>只展示公开地址与官网公示的企业联系方式，不含任何个人联系方式。</li>
        </ul>

        <h4>交易边界</h4>
        <ul class="policy-list">
          <li>本站不下单、不收款、不展示报价、不撮合、不抽成、不保证回复。</li>
        </ul>

        <p class="small muted">
          核实程度的五档含义与来源地图见
          <RouterLink to="/about">数据来源与核实程度</RouterLink>；
          更正与联系走
          <RouterLink to="/service">客户服务</RouterLink>。
        </p>
      </div>
    </section>
  </div>
</template>

<style scoped>
.panel {
  padding: 18px;
}

h3 {
  font-size: 16px;
  margin: 0 0 10px;
}

.section > .card.panel p {
  margin: 0 0 10px;
  line-height: 1.7;
}

.section > .card.panel p:last-child {
  margin-bottom: 0;
}

.beian-inline {
  list-style: none;
  margin: 12px 0 0;
  padding: 0;
  display: grid;
  gap: 8px;
  font-size: 13px;
}

.beian-inline li {
  display: flex;
  align-items: center;
  gap: 8px;
}

.beian-inline a {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  color: var(--muted);
}

.beian-inline a:hover {
  color: var(--brand);
}

.beian-inline img {
  flex: none;
  display: block;
}

.service-list {
  margin: 0 0 14px;
  padding-left: 20px;
  font-size: 13px;
  color: var(--muted);
  display: grid;
  gap: 6px;
}

.policy-list {
  margin: 0 0 14px;
  padding-left: 20px;
  font-size: 13px;
  color: var(--muted);
  display: grid;
  gap: 6px;
}

.section > .card.panel h4 {
  font-size: 13px;
  margin: 0 0 6px;
  color: var(--text);
}

.section > .card.panel h4:not(:first-of-type) {
  margin-top: 16px;
}

.section > .card.panel > .policy-list:last-of-type {
  margin-bottom: 14px;
}
</style>