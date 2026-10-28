<script setup>
import { onMounted, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { api, GRADE_CLASS, GRADE_LABELS } from '../api'
import EnterpriseCard from '../components/EnterpriseCard.vue'
import GradeTag from '../components/GradeTag.vue'

const route = useRoute()
const cluster = ref(null)
const loading = ref(false)
const error = ref('')

async function load(slug) {
  loading.value = true
  error.value = ''
  cluster.value = null
  try {
    cluster.value = await api.cluster(slug)
  } catch (err) {
    error.value = String(err.message || err)
  } finally {
    loading.value = false
  }
}

watch(() => route.params.slug, (slug) => slug && load(slug), { immediate: true })
onMounted(() => {
  if (!route.params.slug) error.value = '缺少集群参数'
})
</script>

<template>
  <div class="container">
    <div v-if="loading" class="loading">加载中…</div>
    <p v-else-if="error" class="notice warn">加载失败：{{ error }}</p>

    <template v-else-if="cluster">
      <section class="section">
        <div class="row">
          <h1 class="title">{{ cluster.name }}</h1>
          <GradeTag
            :grade="cluster.data_grade"
            :label="cluster.data_grade_label"
            :stub="cluster.is_stub"
            :stub-text="cluster.enterprise_count === 0 ? '空壳集群 · 暂无挂载企业' : `空壳集群 · 仅 ${cluster.enterprise_count} 家`"
          />
        </div>

        <div class="stack muted small" style="margin-top: 8px">
          <span v-if="cluster.industry_l1">415X 一级分类：{{ cluster.industry_l1 }}</span>
          <span v-if="cluster.industry_l2">二级细分：{{ cluster.industry_l2 }}</span>
          <span v-if="cluster.scale_tier">规模档：{{ cluster.scale_tier }}</span>
          <span>级别：{{ cluster.tier }} 级</span>
        </div>

        <!-- 集群是官方认定的，不等于这段简介也是官方表述：简介的档位单独标 -->
        <div v-if="cluster.summary" class="summary-block">
          <p class="summary">{{ cluster.summary }}</p>
          <GradeTag
            v-if="cluster.summary_grade"
            :grade="cluster.summary_grade"
            :label="cluster.summary_grade_label"
          />
          <span class="tiny muted">产区简介的来源档位</span>
        </div>

        <div v-if="cluster.is_stub" class="notice warn" style="margin-top: 12px">
          <strong>空壳集群提示</strong>：本站收录判据要求集群页至少能匹配到 10 家企业。
          该集群目前挂载 {{ cluster.enterprise_count }} 家，页面内容尚不完整——
          先补企业库，再完善集群档案，避免出现只有简介、没有企业的空壳页。
        </div>
      </section>

      <section class="section grid cols-2">
        <div class="card panel">
          <h3>主产区与关联行政区</h3>
          <p class="tiny muted">
            集群实体与行政区解耦：核心区/协同区是省里认定的建设主体；市级归口只表示
            这条产区按地市归口（成员散在多个县区），不属于任何一个县 —— 两者都不是产业的地理边界。
          </p>
          <ul class="plain">
            <li v-for="region in cluster.regions" :key="region.code">
              <strong>{{ region.name }}</strong>
              <span class="tag tag-plain tiny">{{ region.role }}</span>
              <span v-if="region.is_primary" class="tag tag-plain tiny">主产区</span>
              <div class="tiny muted">{{ region.path }}</div>
            </li>
          </ul>
          <div v-if="!cluster.regions.length" class="muted small">关联行政区待补</div>
        </div>

        <div class="card panel">
          <h3>来源与核实</h3>
          <ul class="plain">
            <li v-for="(source, index) in cluster.sources" :key="index">
              <span class="tag" :class="GRADE_CLASS[source.grade] || 'tag-plain'">
                {{ GRADE_LABELS[source.grade] || source.grade }}
              </span>
              {{ source.name }}
              <a
                v-if="source.url && /^https?:/.test(source.url)"
                :href="source.url"
                target="_blank"
                rel="noopener noreferrer"
                class="tiny"
              >
                查看来源 →
              </a>
              <span v-else-if="source.url" class="tiny muted">（本地文件：{{ source.url }}）</span>
            </li>
          </ul>
          <div v-if="cluster.honors.length">
            <h4>称号与荣誉</h4>
            <ul class="plain">
              <li v-for="(honor, index) in cluster.honors" :key="index">
                {{ honor.name }} <span class="tiny muted">（{{ honor.granted_by }} · {{ honor.year }}）</span>
              </li>
            </ul>
          </div>
          <div v-if="cluster.metrics.length">
            <h4>规模指标</h4>
            <table class="small">
              <thead>
                <tr><th>数值</th><th>口径</th><th>年份</th><th>来源</th></tr>
              </thead>
              <tbody>
                <tr v-for="(metric, index) in cluster.metrics" :key="index">
                  <td class="mono">{{ metric.value }}{{ metric.unit }}</td>
                  <td>{{ metric.caliber }}</td>
                  <td class="mono">{{ metric.year }}</td>
                  <td class="tiny">{{ metric.source_name }}</td>
                </tr>
              </tbody>
            </table>
          </div>
          <div v-else class="notice small" style="margin-top: 8px">
            规模数字强制存「数值 + 口径 + 年份 + 来源 + 核验日期」五元组。
            该集群尚未摘录到带口径的规模数字，因此<strong>不显示任何规模值</strong>（禁止裸数字）。
          </div>
        </div>
      </section>

      <section class="section">
        <h2>该集群已收录企业（{{ cluster.enterprises.length }} 家）</h2>
        <p class="hint">
          归属规则：主营产品与赛道一致时直接归属；无法判定时归入注册地所在镇街的主导集群，
          并在企业页标注「归属待确认」。归属有误可提交更正。
        </p>
        <div v-if="!cluster.enterprises.length" class="empty">
          该集群暂无挂载企业。按收录判据③，应先把企业库铺到这一层再完善档案。
        </div>
        <div v-else class="grid cols-3">
          <EnterpriseCard v-for="item in cluster.enterprises" :key="item.id" :item="item" />
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

.summary {
  margin: 0 0 6px;
  color: #3c4149;
  max-width: 780px;
}

.summary-block {
  margin-top: 12px;
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 6px;
}

.panel {
  padding: 16px;
}

.panel h3 {
  margin: 0 0 6px;
  font-size: 15px;
}

.panel h4 {
  margin: 14px 0 4px;
  font-size: 14px;
}

ul.plain {
  list-style: none;
  margin: 0;
  padding: 0;
  font-size: 14px;
}

ul.plain li {
  padding: 4px 0;
  border-bottom: 1px dashed var(--border);
}

ul.plain li:last-child {
  border-bottom: none;
}
</style>