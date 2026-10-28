<script setup>
import { computed, onMounted, ref } from 'vue'
import { GRADE_CLASS, GRADE_LABELS } from '../api'

// 产业带（集群）档案的编辑页。写入 data/curated/cluster_edits.json，
// 由灌库流程以最高优先级叠加（官方名单 → 调研叠加 → 本站编辑），所以要重新灌库才生效。
// 注意：重灌只重建数据层，账号与收藏不受影响。
const BASE = import.meta.env.VITE_API_BASE || '/api'
const TOKEN_KEY = 'zanzhu.adminToken'

const token = ref('')
const unlocked = ref(false)
const clusters = ref([])
const keyword = ref('')
const onlyEdited = ref(false)
const current = ref(null)
const saving = ref(false)
const message = ref('')
const error = ref('')

// 编辑表单（只在点开某条时填充）
const form = ref(blank())
const metrics = ref([])
const honors = ref([])

const GRADES = Object.entries(GRADE_LABELS).map(([key, label]) => ({ key, label }))

function blank() {
  return { summary: '', note: '', summary_grade: 'site_curated', aliases: '' }
}

const filtered = computed(() => {
  const key = keyword.value.trim()
  return clusters.value.filter(
    (row) => (!key || row.name.includes(key) || row.slug.includes(key)) && (!onlyEdited.value || row.has_edit),
  )
})

async function call(path, options = {}) {
  const response = await fetch(`${BASE}${path}`, {
    ...options,
    headers: {
      'Content-Type': 'application/json',
      'X-Admin-Token': token.value,
      ...(options.headers || {}),
    },
  })
  const text = await response.text()
  const payload = text ? JSON.parse(text) : null
  if (!response.ok) throw new Error(payload?.detail || `${response.status}`)
  return payload
}

async function load() {
  error.value = ''
  try {
    const data = await call('/admin/clusters')
    clusters.value = data.items
    unlocked.value = true
    localStorage.setItem(TOKEN_KEY, token.value)
  } catch (err) {
    unlocked.value = false
    error.value = String(err.message || err)
  }
}

async function open(row) {
  message.value = ''
  error.value = ''
  try {
    const data = await call(`/admin/clusters/${encodeURIComponent(row.slug)}`)
    const cluster = data.cluster
    current.value = cluster
    form.value = {
      summary: cluster.summary || '',
      note: data.edit?.note || '',
      summary_grade: data.edit?.summary_grade || cluster.summary_grade || 'site_curated',
      aliases: (cluster.aliases || []).join('、'),
    }
    metrics.value = (cluster.metrics || []).map((m) => ({ ...m }))
    honors.value = (cluster.honors || []).map((h) => ({ ...h }))
    window.scrollTo({ top: 0, behavior: 'smooth' })
  } catch (err) {
    error.value = String(err.message || err)
  }
}

function addMetric() {
  metrics.value.push({ value: '', unit: '', caliber: '', year: '', source_name: '', source_grade: 'site_curated' })
}

function addHonor() {
  honors.value.push({ name: '', granted_by: '', year: '', grade: 'site_curated' })
}

async function saveAll() {
  if (!current.value) return
  saving.value = true
  message.value = ''
  error.value = ''
  try {
    const payload = {
      summary: form.value.summary,
      note: form.value.note,
      summary_grade: form.value.summary_grade,
      aliases: form.value.aliases
        .split(/[、,，\n]/)
        .map((a) => a.trim())
        .filter(Boolean),
      metrics: metrics.value,
      honors: honors.value,
    }
    const result = await call(`/admin/clusters/${encodeURIComponent(current.value.slug)}`, {
      method: 'PATCH',
      body: JSON.stringify(payload),
    })
    message.value = `已保存「${current.value.name}」的产业带档案。${result.note}`
    await load()
    current.value = { ...current.value, has_edit: true }
  } catch (err) {
    error.value = String(err.message || err)
  } finally {
    saving.value = false
  }
}

async function revert() {
  if (!current.value) return
  saving.value = true
  message.value = ''
  error.value = ''
  try {
    const result = await call(`/admin/clusters/${encodeURIComponent(current.value.slug)}`, { method: 'DELETE' })
    message.value = `已撤销「${current.value.name}」的站方编辑。${result.note}`
    await load()
    await open(clusters.value.find((row) => row.slug === current.value.slug) || { slug: current.value.slug })
  } catch (err) {
    error.value = String(err.message || err)
  } finally {
    saving.value = false
  }
}

onMounted(() => {
  token.value = localStorage.getItem(TOKEN_KEY) || ''
  if (token.value) load()
})
</script>

<template>
  <div class="container">
    <div class="page-head">
      <h1>产业带档案编辑</h1>
      <p class="lead">
        这里改的是<strong>本站维护的那部分</strong>：产区简介、别名、规模指标、荣誉、核实程度。
        集群名称、档次、地理归属来自官方名单，<strong>不可在此编辑</strong>——改了就等于伪造认定依据。
        写入 <code>data/curated/cluster_edits.json</code>，灌库时以最高优先级叠加，
        因此<strong>保存后需要重新灌库才生效</strong>（重灌只重建数据层，账号与收藏保留）。
      </p>
    </div>

    <section class="section">
      <div v-if="!unlocked" class="card panel">
        <label class="small muted">管理令牌（后端需设置环境变量 <code>ADMIN_TOKEN</code> 才会启用本页）</label>
        <div class="stack" style="margin-top: 8px">
          <input v-model="token" type="password" placeholder="X-Admin-Token" style="width: 280px" />
          <button class="primary" @click="load">解锁</button>
        </div>
        <p v-if="error" class="notice warn" style="margin-top: 12px">无法访问：{{ error }}</p>
      </div>

      <template v-else>
        <p v-if="message" class="notice" style="margin-bottom: 14px">{{ message }}</p>
        <p v-if="error" class="notice warn" style="margin-bottom: 14px">{{ error }}</p>

        <div class="card panel" v-if="current">
          <h3>{{ current.name }} <span class="tiny muted mono">{{ current.slug }}</span></h3>
          <p class="tiny muted">
            集群档位（来自名单，不可编辑）：{{ GRADE_LABELS[current.data_grade] }} ·
            简介档位：{{ GRADE_LABELS[current.summary_grade] || '未标' }} ·
            挂载企业 {{ current.enterprise_count }} 家 ·
            <span v-if="current.regions?.length">{{ current.regions.map((r) => r.name + ' ' + r.role).join('、') }}</span>
          </p>

          <div class="form-grid" style="margin-top: 10px">
            <label class="wide">
              产区简介
              <textarea v-model="form.summary" rows="4" placeholder="如实描述产区与依据；数字请放到下面的规模指标里"></textarea>
            </label>
            <label>别名（顿号或逗号分隔）<input v-model="form.aliases" type="text" placeholder="义乌无缝织造、中国无缝针织服装名城" /></label>
            <label>
              简介的核实程度<span class="tiny muted">（指这段文字本身，不影响集群档位）</span>
              <select v-model="form.summary_grade">
                <option v-for="g in GRADES" :key="g.key" :value="g.key">{{ g.label }}</option>
              </select>
            </label>
            <label class="wide">编辑说明<input v-model="form.note" type="text" placeholder="例如：依据 2026 年县统计公报修订；联系方式见 xx" /></label>
          </div>

          <h4 style="margin-top: 18px">规模指标（数值必须带口径与年份，否则前端不显示）</h4>
          <table class="card list">
            <thead>
              <tr><th>数值</th><th>单位</th><th>口径</th><th>年份</th><th>来源</th><th>来源档位</th><th></th></tr>
            </thead>
            <tbody>
              <tr v-for="(m, i) in metrics" :key="i">
                <td><input v-model="m.value" type="text" style="width: 90px" /></td>
                <td><input v-model="m.unit" type="text" style="width: 60px" /></td>
                <td><input v-model="m.caliber" type="text" style="width: 170px" placeholder="设备能力口径" /></td>
                <td><input v-model="m.year" type="text" style="width: 80px" /></td>
                <td><input v-model="m.source_name" type="text" style="width: 220px" /></td>
                <td>
                  <select v-model="m.source_grade">
                    <option v-for="g in GRADES" :key="g.key" :value="g.key">{{ g.label }}</option>
                  </select>
                </td>
                <td><button class="tiny-btn" @click="metrics.splice(i, 1)">删除</button></td>
              </tr>
            </tbody>
          </table>
          <button class="tiny-btn" style="margin-top: 8px" @click="addMetric">+ 加一条指标</button>

          <h4 style="margin-top: 18px">称号与荣誉（必须写授予主体）</h4>
          <table class="card list">
            <thead>
              <tr><th>名称</th><th>授予主体</th><th>年份</th><th>核实程度</th><th></th></tr>
            </thead>
            <tbody>
              <tr v-for="(h, i) in honors" :key="i">
                <td><input v-model="h.name" type="text" style="width: 220px" /></td>
                <td><input v-model="h.granted_by" type="text" style="width: 240px" placeholder="浙江省经济和信息化厅" /></td>
                <td><input v-model="h.year" type="text" style="width: 80px" /></td>
                <td>
                  <select v-model="h.grade">
                    <option v-for="g in GRADES" :key="g.key" :value="g.key">{{ g.label }}</option>
                  </select>
                </td>
                <td><button class="tiny-btn" @click="honors.splice(i, 1)">删除</button></td>
              </tr>
            </tbody>
          </table>
          <button class="tiny-btn" style="margin-top: 8px" @click="addHonor">+ 加一条荣誉</button>

          <div class="stack" style="margin-top: 16px">
            <button class="primary" :disabled="saving" @click="saveAll">{{ saving ? '保存中…' : '保存到 cluster_edits.json' }}</button>
            <button :disabled="saving" @click="revert">撤销站方编辑（回退到名单值）</button>
            <button @click="current = null">关闭</button>
          </div>
        </div>

        <div class="row" style="margin-top: 20px">
          <h3>产业集群（{{ filtered.length }} / {{ clusters.length }}）</h3>
          <div class="stack">
            <input v-model="keyword" type="search" placeholder="筛选集群名或 slug" />
            <label class="tiny muted" style="flex-direction: row; align-items: center; gap: 6px">
              <input v-model="onlyEdited" type="checkbox" style="width: auto" /> 只看已编辑
            </label>
          </div>
        </div>

        <table class="card list">
          <thead>
            <tr><th>集群</th><th>档次</th><th>地区</th><th>企业</th><th>指标/荣誉</th><th>编辑</th><th></th></tr>
          </thead>
          <tbody>
            <tr v-for="row in filtered" :key="row.slug">
              <td>
                <strong>{{ row.name }}</strong>
                <div class="tiny muted mono">{{ row.slug }}</div>
              </td>
              <td><span class="tag" :class="GRADE_CLASS[row.data_grade] || 'tag-plain'">{{ GRADE_LABELS[row.data_grade] }}</span></td>
              <td class="small muted">{{ row.city }}{{ row.district }}</td>
              <td class="small muted mono">{{ row.enterprise_count }}</td>
              <td class="small muted mono">{{ row.metric_count }} / {{ row.honor_count }}</td>
              <td class="small muted">
                <template v-if="row.has_edit">
                  <span class="tag tag-plain tiny">已编辑 {{ row.edited_at.slice(0, 10) }}</span>
                  <div class="tiny muted">{{ row.edited_fields.join('、') }}</div>
                </template>
                <span v-else class="muted">—</span>
              </td>
              <td><button class="tiny-btn" @click="open(row)">编辑</button></td>
            </tr>
          </tbody>
        </table>
      </template>
    </section>
  </div>
</template>

<style scoped>
.panel {
  padding: 16px;
}

.row {
  display: flex;
  justify-content: space-between;
  align-items: flex-end;
  gap: 12px;
  flex-wrap: wrap;
}

h4 {
  font-size: 14px;
  margin: 16px 0 6px;
}

input,
select,
textarea {
  width: 100%;
}

table input,
table select {
  width: 100%;
}
</style>
