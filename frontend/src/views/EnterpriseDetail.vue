<script setup>
import { onMounted, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { api, ENRICH_LABELS, GRADE_CLASS, GRADE_LABELS, PARENT_SITE_KINDS, tierLabel } from '../api'
import { applySeo, enterpriseJsonLd } from '../seo'
import GradeTag from '../components/GradeTag.vue'
import SourceTag from '../components/SourceTag.vue'
import FavoriteButton from '../components/FavoriteButton.vue'

const route = useRoute()
const enterprise = ref(null)
const loading = ref(false)
const error = ref('')

async function load(id) {
  loading.value = true
  error.value = ''
  enterprise.value = null
  try {
    enterprise.value = await api.enterprise(id)
    const row = enterprise.value
    applySeo({
      title: row.name,
      description:
        [
          [row.city, row.district, row.town].filter(Boolean).join(' '),
          (row.products || []).slice(0, 3).join('、'),
          row.data_grade_label,
        ]
          .filter(Boolean)
          .join('，') + '。联系方式与来源逐条标注。',
      path: `/enterprises/${row.id}`,
      // 集团站 / 母公司站的域名不是这家企业的官网，塞进 url 会张冠李戴
      jsonLd: enterpriseJsonLd(row, {
        isParentSite: PARENT_SITE_KINDS.has(row.website_kind),
      }),
    })
  } catch (err) {
    error.value = String(err.message || err)
  } finally {
    loading.value = false
  }
}

watch(() => route.params.id, (id) => id && load(id), { immediate: true })

onMounted(() => {
  if (!route.params.id) error.value = '缺少企业参数'
})
</script>

<template>
  <div class="container">
    <div v-if="loading" class="loading">加载中…</div>
    <p v-else-if="error" class="notice warn">加载失败：{{ error }}</p>

    <template v-else-if="enterprise">
      <section class="section">
        <div class="row">
          <h1 class="title">{{ enterprise.name }}</h1>
          <div class="stack">
            <FavoriteButton :enterprise-id="enterprise.id" />
            <GradeTag :grade="enterprise.data_grade" :label="enterprise.data_grade_label" />
          </div>
        </div>

        <div class="stack muted small" style="margin-top: 8px">
          <span class="mono">
            {{ [enterprise.city, enterprise.district, enterprise.town].filter(Boolean).join(' · ') || '所属地区待补' }}
          </span>
          <span v-if="enterprise.region_source === 'name_prefix'" class="tag tag-plain tiny">
            地区按企业名称前缀推断，待核实
          </span>
          <span v-else-if="enterprise.region_source === 'research_list'" class="tag tag-plain tiny">
            地区来自第三方调研清单
          </span>
          <span v-else-if="enterprise.region_source === 'out_of_province'" class="tag tag-plain tiny">
            省外产区 · 仅作对比参照，不参与浙江产区统计
          </span>
          <!-- 这一档是「清单说是对标龙头，可它在浙江」（天堂伞在萧山、均胜在宁波、
               森马与人本在温州）。它不该被写成省外 —— 那会把浙江企业划出浙江统计。 -->
          <span v-else-if="enterprise.region_source === 'benchmark'" class="tag tag-plain tiny">
            对标龙头 · 不是该产区的企业，不参与产区统计（清单标注）
          </span>
        </div>

        <div class="stack tiny" style="margin-top: 8px">
          <RouterLink
            v-for="cluster in enterprise.clusters"
            :key="cluster.slug"
            class="tag tag-plain"
            :to="{ name: 'cluster', params: { slug: cluster.slug } }"
          >
            {{ cluster.name }}{{ cluster.is_primary ? '（主集群）' : '' }}
          </RouterLink>
        </div>

        <p v-if="enterprise.attribution_note" class="notice warn small" style="margin-top: 12px">
          {{ enterprise.attribution_note }} —— 归属规则已公开，如归属有误请通过更正通道提交依据。
        </p>
      </section>

      <section class="section grid cols-2">
        <div class="card panel">
          <h3>公开联系方式</h3>
          <p class="tiny muted">
            只展示官网公示的企业联系方式；手机号一律不采集、不展示，本页不含任何个人联系方式。
          </p>
          <table class="small">
            <tbody>
              <tr v-if="enterprise.address_registered">
                <th>注册地</th>
                <td>
                  {{ enterprise.address_registered }}
                  <span class="tiny muted">（工商登记，可能与实际厂址不同）</span>
                </td>
              </tr>
              <tr>
                <th style="width: 96px">官网</th>
                <td>
                  <a v-if="enterprise.website" :href="enterprise.website" target="_blank" rel="noopener noreferrer">
                    {{ enterprise.website }}
                  </a>
                  <span v-else class="muted">未核实到官网</span>
                  <SourceTag
                    v-if="enterprise.contact_sources?.website"
                    :label="enterprise.contact_sources.website"
                    style="margin-left: 6px"
                  />
                  <!-- 链接指向的是集团站/母公司站时要说清楚，否则采购商会以为进的是工厂自己的站 -->
                  <div v-if="enterprise.website_kind && enterprise.website_kind !== '企业官网'" class="tiny muted">
                    链接类型：{{ enterprise.website_kind }}
                    <template v-if="PARENT_SITE_KINDS.has(enterprise.website_kind)">
                      —— 指向所属集团/母公司站点，不是本企业独立官网
                    </template>
                  </div>
                </td>
              </tr>
              <tr>
                <th>公开电话</th>
                <td class="mono">
                  <template v-if="enterprise.public_phone">
                    {{ enterprise.public_phone }}
                    <SourceTag
                      v-if="enterprise.contact_sources?.phone"
                      :label="enterprise.contact_sources.phone"
                      style="margin-left: 6px"
                    />
                    <!-- 号码性质（官网总机 / 第三方总机 / 待核）：「官网上印着」不等于「打得通」 -->
                    <div v-if="enterprise.phone_nature && enterprise.phone_nature !== '—'" class="tiny muted">
                      电话性质：{{ enterprise.phone_nature }}
                    </div>
                  </template>
                  <span v-else class="muted">
                    —<span class="tiny"> 未从官网核实到总机/400 号码</span>
                  </span>
                </td>
              </tr>
              <tr>
                <th>公开邮箱</th>
                <td>
                  <template v-if="enterprise.public_email">
                    {{ enterprise.public_email }}
                    <SourceTag
                      v-if="enterprise.contact_sources?.email"
                      :label="enterprise.contact_sources.email"
                      style="margin-left: 6px"
                    />
                  </template>
                  <span v-else class="muted">—</span>
                </td>
              </tr>
              <tr>
                <th>公开地址</th>
                <td>
                  <template v-if="enterprise.address">
                    {{ enterprise.address }}
                    <SourceTag
                      v-if="enterprise.contact_sources?.address"
                      :label="enterprise.contact_sources.address"
                      style="margin-left: 6px"
                    />
                  </template>
                  <span v-else>{{ enterprise.city }}{{ enterprise.district }}<span class="tiny muted">（按名单所属地区）</span></span>
                </td>
              </tr>
            </tbody>
          </table>
          <p v-if="enterprise.manual_note" class="tiny muted" style="margin-top: 8px">
            人工补充备注：{{ enterprise.manual_note }}
          </p>
          <p v-if="enterprise.source_url" class="tiny muted" style="margin-top: 8px">
            联系方式来源：
            <a :href="enterprise.source_url" target="_blank" rel="noopener noreferrer">{{ enterprise.source_url }}</a>
            <span v-if="enterprise.verified_at"> · 核验日期 {{ String(enterprise.verified_at).slice(0, 10) }}</span>
          </p>
          <p v-else class="tiny muted" style="margin-top: 8px">
            尚未核实到官网，联系方式留空而不猜测——这类企业由人工补充兜底。
          </p>
        </div>

        <div class="card panel">
          <h3>资质与来源</h3>
          <ul class="plain">
            <li v-for="(honor, index) in enterprise.honors" :key="index">
              <GradeTag :grade="honor.grade || 'official'" :label="GRADE_LABELS[honor.grade || 'official']" />
              {{ honor.name }}
              <div class="tiny muted">来源：{{ honor.granted_by }}</div>
              <div v-if="honor.verify_tier" class="tiny muted">
                该名单内的核实档位：{{ tierLabel(honor.verify_tier) }}
              </div>
            </li>
          </ul>
          <p class="tiny muted">
            名单原文只给企业名称与所属地区，未逐条给出官网与联系方式；
            联系方式的核实程度以联系方式处的逐字段标注为准。
          </p>
          <div class="stack tiny" style="margin-top: 6px">
            <span class="tag tag-plain">资料完整度 {{ enterprise.completeness }}%</span>
            <!-- 完整度会随「有产品」上升，所以点明它由哪几项算出来 ——
                 否则读者看不出为什么这家 40%、那家 80%。 -->
            <span class="tiny muted">（官网 / 电话 / 邮箱 / 地址 / 产品的齐全程度）</span>
            <span v-if="enterprise.enrich_status" class="tag tag-plain">
              官网核验：{{ ENRICH_LABELS[enterprise.enrich_status] || enterprise.enrich_status }}
            </span>
          </div>
        </div>
      </section>

      <section class="section" v-if="enterprise.founded_year || enterprise.registered_capital || enterprise.verify_tier">
        <h2>企业档案（来自名单原文）</h2>
        <table class="small card panel">
          <tbody>
            <tr v-if="enterprise.founded_year">
              <th style="width: 110px">成立年份</th>
              <td>{{ enterprise.founded_year }} 年</td>
            </tr>
            <tr v-if="enterprise.registered_capital">
              <th>注册资本</th>
              <td>
                {{ enterprise.registered_capital }}
                <span class="tiny muted">（按名单原文照录，未换算）</span>
              </td>
            </tr>
            <tr v-if="enterprise.business_model">
              <th>产区业态</th>
              <td>
                {{ enterprise.business_model }}
                <span class="tiny muted">（代工型与品牌型的字段需求不同，分开标注）</span>
              </td>
            </tr>
            <tr v-if="enterprise.verify_tier">
              <th>核实档位</th>
              <td>
                {{ tierLabel(enterprise.verify_tier) }}
                <span class="tiny muted">（该企业在来源名单内被核实到什么程度）</span>
              </td>
            </tr>
          </tbody>
        </table>
        <p class="tiny muted">
          这些字段不是联系方式：它们说明企业的基本情况，来源同上方的资质列表。
        </p>
      </section>

      <section class="section" v-if="enterprise.products.length">
        <h2>主营产品与业务</h2>
        <!-- 产品一行一条、可选中复制：采购商常常要把品名带出去询价，
             挤成一段散文就没法用了。
             什么叫「收录产品」也说清楚 —— 此前只写「（名单原文）」，读者不知道
             这是名单作者写的，还是我们核实过的。 -->
        <p class="hint">
          下列品名<strong>照录来源名单原文</strong>，未改写、未归类；它是这家企业<strong>在该名单里登记的品类</strong>，不等于企业的全部业务范围。产品名里带型号括注（如「（A46、A42）」）的，括注同样来自原文。名单出处与核实程度见上方「资质与来源」。
        </p>
        <ul class="product-list panel">
          <li v-for="(product, index) in enterprise.products" :key="index">
            <span class="product-index mono">{{ index + 1 }}</span>
            <span class="product-text">{{ product }}</span>
          </li>
        </ul>
        <p class="tiny muted" style="margin-top: 8px">
          名单出处见上方「资质与来源」；该名单的核实程度也标在那里。
        </p>
      </section>

      <section class="section" v-else>
        <h2>主营产品与业务</h2>
        <div class="empty-box">
          <strong>来源名单未登记主营产品。</strong>
          <p class="small muted" style="margin: 6px 0 0">
            本站只照录名单写下的字段，<strong>不为企业编造一个产品名</strong>。
            该企业所在的名单（见上方「资质与来源」）只有主体信息与地区，
            没有产品列；这类企业的品类需要按官网或工商经营范围另行核实。
          </p>
        </div>
      </section>

      <section class="section">
        <div class="card panel">
          <h3>企业自报与更正</h3>
          <p class="small muted">
            本站不接收付费入驻、不卖排名、不做付费认证与等级背书。企业可通过公开信息核对后申请完善资料，
            也可对已有字段提交更正或下线申请（需附页面链接、需更正字段与可核依据）。
          </p>
          <div class="notice small">
            边界声明：本站不提供报价、不撮合交易、不抽成、不保证回复。采购需求不在站内转达，
            请直接使用上方企业官网公示的联系渠道。
          </div>
        </div>
      </section>
    </template>
  </div>
</template>

<style scoped>
.title {
  font-size: 24px;
  margin: 0;
}

.panel {
  padding: 16px;
}

.panel h3 {
  margin: 0 0 6px;
  font-size: 15px;
}

ul.plain {
  list-style: none;
  margin: 0;
  padding: 0;
  font-size: 14px;
}

ul.plain li {
  padding: 5px 0;
  border-bottom: 1px dashed var(--border);
}

ul.plain li:last-child {
  border-bottom: none;
}

/* --------------------------------------------------------------------------
   主营产品：一行一条、编号 + 可选中文本。
   编号在这里是**真的序号**（这些是并列的登记品名，数得清），
   不是为了好看加上的装饰性 01/02/03。
   -------------------------------------------------------------------------- */
.product-list {
  list-style: none;
  margin: 0;
  padding: 4px 0;
}

.product-list li {
  display: flex;
  gap: 10px;
  align-items: baseline;
  padding: 9px 16px;
}

.product-list li + li {
  border-top: 1px solid var(--rule);
}

.product-index {
  flex: 0 0 auto;
  min-width: 1.4em;
  font-size: 12px;
  color: var(--ink-3);
  text-align: right;
}

.product-text {
  font-size: 15px;
  line-height: 1.65;
  /* 长品名要能换行；同时留出可复制的边界感 */
  word-break: break-word;
}

/* 空状态：说清「为什么空」以及去哪找，不做成一句「暂无数据」。
   这一站的数据纪律是「空就空，并写明原因」，界面必须一致。 */
.empty-box {
  border: 1px dashed var(--rule-strong);
  border-radius: var(--r-md);
  background: #fbfbfa;
  padding: 16px 18px;
  font-size: 14px;
  color: var(--ink-2);
}
</style>