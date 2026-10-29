<script setup>
import { onMounted, ref } from 'vue'
import { api, GRADE_LABELS } from '../api'
import ClusterRow from '../components/ClusterRow.vue'
import GradeTag from '../components/GradeTag.vue'

const stats = ref(null)
const tracks = ref([])
const towns = ref([])
const error = ref('')

onMounted(async () => {
  try {
    const [statsData, trackData, clusterData] = await Promise.all([
      api.stats(),
      api.tracks(),
      api.clusters({ tier: 'C', page_size: 8, sort: 'name' }),
    ])
    stats.value = statsData
    tracks.value = trackData
    towns.value = clusterData.items
  } catch (err) {
    error.value = String(err.message || err)
  }
})
</script>

<template>
  <div class="container">
    <!-- 首屏：不用「大数字 + 小标签 + 渐变」的默认款。
         只留**一句判断**（这个库到底在说什么）+ 两个动作，下面直接接合计栏 ——
         构成方式取自登记册的合计栏，而不是仪表盘。
         句下那段解释性导语（「大唐袜业、濮院毛衫……每一笔来路」）已按要求删除：
         这些信息在「收录所依据的名单」「集群赛道」「镇街级块状经济」三节里逐条给出，
         放在首屏只是把结论重复一遍。 -->
    <section class="section hero">
      <h1>浙江的产业优势不在单个企业，在镇。</h1>
      <div class="hero-actions">
        <RouterLink class="btn primary-btn" to="/enterprises">检索企业</RouterLink>
        <RouterLink class="btn" to="/map">打开产业地图</RouterLink>
      </div>
      <p v-if="error" class="notice warn" style="margin-top: 16px">
        接口未就绪：{{ error }}（请先启动后端 <code>uvicorn app.main:app</code>）
      </p>
    </section>

    <section v-if="stats" class="section">
      <div class="kpi-band">
        <div class="kpi-cell lead-cell">
          <div class="value">{{ stats.enterprise_total.toLocaleString() }}</div>
          <div class="label">
            已收录企业<br />官方名单 {{ stats.enterprise_official }} · 调研与政府公开
            {{ stats.enterprise_total - stats.enterprise_official }}
          </div>
        </div>
        <div class="kpi-cell">
          <div class="value">{{ stats.cluster_total }}</div>
          <div class="label">产业集群档案<br />其中 {{ stats.cluster_official }} 个为官方口径</div>
        </div>
        <div class="kpi-cell">
          <div class="value">{{ stats.enterprise_with_website }}</div>
          <div class="label">已核实官网<br />附来源链接与核验日期</div>
        </div>
        <div class="kpi-cell">
          <div class="value">{{ stats.enterprise_with_phone }}</div>
          <div class="label">已核实公开电话<br />手机号一律不采集</div>
        </div>
      </div>
    </section>

    <section class="section" v-if="stats">
      <h2>收录所依据的名单</h2>
      <div class="source-list">
        <div v-for="entry in stats.list_breakdown" :key="entry.name" class="source-row">
          <span class="source-name">{{ entry.name }}</span>
          <span class="mono source-count">{{ entry.count }}</span>
          <GradeTag
            :grade="entry.grade"
            :label="entry.grade_label || GRADE_LABELS[entry.grade] || entry.grade"
          />
        </div>
      </div>
      <p class="note-block" style="margin-top: 14px">
        名单口径声明：本页数字出自不同名单、不同发布主体、不同认定层级，
        不可相加成「浙江产业群总数」，也不可跨名单比较规模。
      </p>
    </section>

    <section class="section" v-if="tracks.length">
      <h2>“415X” 集群赛道</h2>
      <div class="track-lines">
        <RouterLink
          v-for="track in tracks"
          :key="track.slug"
          class="track-line"
          :to="{ name: 'cluster', params: { slug: track.slug } }"
        >
          <span>{{ track.name }}</span>
          <span class="mono muted">{{ track.enterprise_count }} 家</span>
          <span class="tiny muted">{{ track.sector || '未分类' }}</span>
        </RouterLink>
      </div>
    </section>

    <section class="section" v-if="towns.length">
      <h2>镇街级块状经济</h2>
      <div class="register">
        <ClusterRow v-for="cluster in towns" :key="cluster.slug" :item="cluster" />
      </div>
      <div class="stack" style="margin-top: 14px">
        <RouterLink class="btn" to="/clusters?tier=C">查看全部镇街集群</RouterLink>
      </div>
    </section>
  </div>
</template>

<style scoped>
.hero {
  padding-top: 34px;
}

.hero h1 {
  font-size: var(--fs-lg);
  margin: 0 0 14px;
  line-height: 1.45;
  max-width: 24em;
}

.hero-actions {
  display: flex;
  gap: 10px;
  margin-top: 20px;
  flex-wrap: wrap;
}

.btn {
  display: inline-block;
  padding: 8px 18px;
  border: 1px solid var(--rule-strong);
  border-radius: var(--r-xs);
  background: #fff;
  font-size: var(--fs-sm);
}

.primary-btn {
  background: var(--seal);
  border-color: var(--seal);
  color: #fff;
}

.primary-btn:hover {
  color: #fff;
  opacity: 0.92;
}

/* 名单清单：细线行。名字可长可短（「临安区规上企业基本信息（政府公开数据）」），
   所以名字占满剩余宽度，数字与档位定宽右对齐 —— 这样一列数字能直接扫下来。 */
.source-list {
  border-top: 1px solid var(--rule-strong);
}

.source-row {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 72px 116px;
  align-items: center;
  gap: 14px;
  padding: 11px 4px;
  border-bottom: 1px solid var(--rule);
}

.source-name {
  font-size: var(--fs-sm);
}

.source-count {
  text-align: right;
  font-size: var(--fs-base);
  font-weight: 600;
}

/* 赛道：三条细线一行，信息密度高但不做报纸密排。 */
.track-lines {
  border-top: 1px solid var(--rule-strong);
}

.track-line {
  display: grid;
  grid-template-columns: minmax(0, 1.4fr) 80px minmax(0, 1fr);
  gap: 14px;
  align-items: baseline;
  padding: 10px 4px;
  border-bottom: 1px solid var(--rule);
  font-size: var(--fs-sm);
}

.track-line:hover {
  background: #fbfbfa;
  color: var(--seal);
}

@media (max-width: 640px) {
  .hero h1 {
    font-size: var(--fs-lg);
  }
  .source-row,
  .track-line {
    grid-template-columns: 1fr;
    gap: 4px;
  }
  .source-count {
    text-align: left;
  }
}
</style>