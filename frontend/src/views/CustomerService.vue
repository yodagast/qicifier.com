<script setup>
import { CONTACT_EMAIL, COMPANY_NAME, GONGAN_NO, GONGAN_URL, ICP_NO, ICP_URL } from '../site'

// 「客户服务」页。三条内容约定：
//   1. 本站不参与交易，因此**不写**配送、退换一类话术 —— 写了就等于承认自己是
//      销售方，与「不下单、不收款、不撮合」的对外承诺自相矛盾；
//   2. 隐私条款必须与 /about 的「账号与隐私」「退出边界」两节同口径，
//      两处说法不同就是最典型的不合规与失真；
//   3. 联系方式只有 contact 邮箱一个（见 src/site.js），不列客服电话。
</script>

<template>
  <div class="container">
    <div class="page-head">
      <h1>客户服务</h1>
      <p class="lead">
        本站是公开信息整理工具，不参与交易。这里的每一条说明都对应一个可执行的动作。
      </p>
    </div>

    <section id="privacy" class="section">
      <div class="card panel">
        <h3>隐私条款</h3>
        <ul class="boundary-list">
          <li>
            本站<strong>不采集任何个人联系方式</strong>：企业页只展示官网公示的总机 / 400 与公开邮箱，
            手机号一律不采集、不展示；邮箱本地部分若本身就是手机号，同样按个人联系方式处理并剔除。
          </li>
          <li>注册只要求用户名与密码，不要求实名、不要求手机号。密码不会以明文保存。</li>
          <li>收藏与备注只对你本人可见，用于你自己整理线索，不对外展示、不参与排序。</li>
          <li>站内问答（规划中）只检索站内实体库，不访问外部网络，并标注「内容由 AI 生成」。</li>
        </ul>
      </div>
    </section>

    <section id="contact" class="section">
      <div class="card panel">
        <h3>联系我们</h3>
        <p class="small muted">
          本站只公开下面这一个联系邮箱，不提供客服电话，也不接收个人联系方式。
        </p>
        <table class="contact-table">
          <tbody>
            <tr>
              <th scope="row">联系邮箱</th>
              <td>
                <a
                  class="mail"
                  :href="`mailto:${CONTACT_EMAIL}`"
                  :title="`发送邮件至 ${CONTACT_EMAIL}`"
                >
                  {{ CONTACT_EMAIL }}
                </a>
              </td>
            </tr>
            <tr>
              <th scope="row">信息更正</th>
              <td>
                邮件里附上可核依据（官网页面、公示原文、营业执照公示信息等），或先看
                <RouterLink to="/about">「数据来源与核实程度」</RouterLink>公开的更正通道。
              </td>
            </tr>
            <tr>
              <th scope="row">主办单位</th>
              <td>{{ COMPANY_NAME }}</td>
            </tr>
            <tr>
              <th scope="row">ICP 备案号</th>
              <td>
                <a :href="ICP_URL" target="_blank" rel="noopener noreferrer">{{ ICP_NO }}</a>
              </td>
            </tr>
            <tr>
              <th scope="row">公安备案号</th>
              <td>
                <a :href="GONGAN_URL" target="_blank" rel="noreferrer noopener">{{ GONGAN_NO }}</a>
              </td>
            </tr>
            <tr>
              <th scope="row">服务范围</th>
              <td>
                仅限本站公开信息的查询与更正。不接待下单、询价、招商、合作与推广。
              </td>
            </tr>
          </tbody>
        </table>
        <p class="small muted" style="margin-top: 12px">
          本站不下单、不收款、不展示报价、不撮合、不抽成、不保证回复；
          不收「入驻门票」、不卖排名、不接受竞价、不做付费认证与等级背书。
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

.boundary-list {
  margin: 10px 0 0;
  padding-left: 20px;
  font-size: 13px;
  color: var(--muted);
  display: grid;
  gap: 7px;
}

.mail {
  color: var(--text);
  word-break: break-all;
}

.mail:hover {
  color: var(--brand);
}

.contact-table {
  width: 100%;
  border-collapse: collapse;
  font-size: 13px;
}

.contact-table th,
.contact-table td {
  text-align: left;
  padding: 9px 12px;
  border-bottom: 1px solid var(--border);
  vertical-align: top;
}

.contact-table th {
  width: 110px;
  font-weight: 600;
  color: var(--text);
  white-space: nowrap;
}

.contact-table tr:last-child th,
.contact-table tr:last-child td {
  border-bottom: none;
}

.contact-table td {
  color: var(--muted);
}
</style>