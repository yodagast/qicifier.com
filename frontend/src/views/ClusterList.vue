<script setup>
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { api } from '../api'
import ClusterRow from '../components/ClusterRow.vue'
import Pager from '../components/Pager.vue'

const route = useRoute()
const router = useRouter()

const items = ref([])
const meta = ref({ total: 0, page: 1, page_size: 20, pages: 0 })
const loading = ref(false)
const error = ref('')

const filters = ref({
  q: route.query.q || '',
  tier: route.query.tier || '',
  data_grade: route.query.data_grade || '',
  city: route.query.city || '',
  town: route.query.town || '',
  sort: route.query.sort || 'tier',
  page: Number(route.query.page || 1),
})

// 表头五格。`sort` 为 null 的两格不可排序：主产区是三个字段拼出来的文字，
// 按字典序排对读者没有意义（「杭州市」排在「金华市」前面只是因为字形）；
// 核实程度是钤记标签，不是可比的量。
//
// 每格自带方向：后端每种排序只有一个方向（集群名、产业升序，企业数降序），
// 所以箭头与 aria-sort 必须**按列**给，不能一律当成降序 ——
// 「按集群名」标成 descending 会让读屏用户以为它在倒序。
const SORT_OPTIONS = [
  { value: 'tier', label: '按档位（默认）' },
  { value: 'name', label: '按集群名' },
  { value: 'industry', label: '按产业' },
  { value: 'count', label: '按企业数（多→少）' },
]

const HEAD_CELLS = [
  { key: 'name', area: 'c-name', label: '集群', sort: 'name', title: '按集群名', arrow: '▴', aria: 'ascending' },
  { key: 'industry', area: 'c-industry', label: '产业', sort: 'industry', title: '按产业', arrow: '▴', aria: 'ascending' },
  { key: 'region', area: 'c-region', label: '主产区', sort: null },
  { key: 'count', area: 'c-count', label: '企业数', sort: 'count', title: '按企业数（多→少）', arrow: '▾', aria: 'descending', right: true },
  { key: 'grade', area: 'c-grade', label: '核实程度', sort: null, right: true },
]

const towns = ref([])

async function load() {
  loading.value = true
  error.value = ''
  try {
    const data = await api.clusters({ ...filters.value, page_size: 20 })
    items.value = data.items
    meta.value = data.meta
  } catch (err) {
    error.value = String(err.message || err)
  } finally {
    loading.value = false
  }
}

function apply() {
  filters.value.page = 1
  sync()
}

function sync() {
  const query = {}
  for (const [key, value] of Object.entries(filters.value)) {
    if (!value) continue
    if (key === 'page' && value === 1) continue
    // 默认排序不写进地址栏：它本来就是「什么都不传」时的那个顺序，
    // 写进去只会让每个链接都多一串没有信息量的参数。
    if (key === 'sort' && value === 'tier') continue
    query[key] = value
  }
  router.replace({ name: 'clusters', query })
}

function setPage(page) {
  filters.value.page = page
  sync()
}

/** 点表头：同一列再点一次就切回默认（按档位）—— 不必为了复位去找筛选框。 */
function sortBy(key) {
  filters.value.sort = filters.value.sort === key ? 'tier' : key
  filters.value.page = 1
  sync()
}

/** 这一列当前的排序方向；null = 没按它排（aria-sort 的 none）。 */
function ariaSort(cell) {
  return filters.value.sort === cell.sort ? cell.aria : 'none'
}

/** 悬停提示说清「已按此列排序」与「再点一次恢复」，否则读者不知道再点会发生什么。 */
function sortTitle(cell) {
  return filters.value.sort === cell.sort ? `${cell.title}（已按此列排序，再点一次恢复）` : cell.title
}

watch(
  () => route.query,
  (next) => {
    const incoming = {
      q: next.q || '',
      tier: next.tier || '',
      data_grade: next.data_grade || '',
      city: next.city || '',
      town: next.town || '',
      sort: next.sort || 'tier',
      page: Number(next.page || 1),
    }
    if (JSON.stringify(incoming) !== JSON.stringify(filters.value)) {
      filters.value = incoming
    }
    load()
  },
  { immediate: true },
)

onMounted(async () => {
  try {
    const tree = await api.regions()
    const list = []
    for (const city of tree[0]?.children || []) {
      for (const district of city.children || []) {
        for (const town of district.children || []) {
          if (town.has_industry) list.push({ code: town.code, label: `${city.name} / ${district.name} / ${town.name}` })
        }
      }
    }
    towns.value = list
  } catch {
    /* 产业地图数据缺失不影响列表 */
  }
})

const tierLabel = computed(() => ({ A: 'A 级 必收', B: 'B 级 应收', C: 'C 级 长尾' }))
</script>

<template>
  <div class="container">
    <div class="page-head">
      <h1>产业集群</h1>
      <p class="lead">
        A 级 = 有官方认定的 415X 赛道，按完整档案收录；C 级 = 无官方名单的镇街级块状经济，属站方整理，单独标档，绝不混进「官方来源」。镇街级产区的可信度靠<strong>逐行核实档位</strong>交代（A 官网公示 / B 工商可核 / C 线索未核），且「有官网」不等于「打得通电话」——每条号码都带性质标注。
      </p>
    </div>

    <section class="section">
      <div class="card filters">
        <input v-model="filters.q" type="search" placeholder="集群名 / 别名 / 产业关键词" @keyup.enter="apply" />
        <select v-model="filters.tier" @change="apply">
          <option value="">全部级别</option>
          <option value="A">A 级 必收</option>
          <option value="C">C 级 长尾</option>
        </select>
        <select v-model="filters.data_grade" @change="apply">
          <option value="">全部核实程度</option>
          <option value="official">官方认定</option>
          <option value="site_curated">站方整理</option>
        </select>
        <select v-model="filters.city" @change="apply">
          <option value="">全部设区市</option>
          <option v-for="city in ['杭州市','宁波市','温州市','嘉兴市','湖州市','绍兴市','金华市','衢州市','舟山市','台州市','丽水市']" :key="city" :value="city">
            {{ city }}
          </option>
        </select>
        <select v-model="filters.town" @change="apply">
          <option value="">全部镇街</option>
          <option v-for="town in towns" :key="town.code" :value="town.label.split(' / ').pop()">
            {{ town.label }}
          </option>
        </select>
        <!-- 排序同时给两个入口，是有意的：
             表头点选在宽屏上最直接（就在要比的那一列上），但表头在 ≤1080px
             会被收起来（列位重排，留着会错位）—— 只有表头一个入口，
             笔记本与手机上的读者就完全无法排序。下拉是那个宽度无关的入口。 -->
        <select v-model="filters.sort" aria-label="排序方式" @change="apply">
          <option v-for="opt in SORT_OPTIONS" :key="opt.value" :value="opt.value">{{ opt.label }}</option>
        </select>
        <button class="primary" @click="apply">筛选</button>
      </div>

      <p v-if="error" class="notice warn">接口异常：{{ error }}</p>
      <div v-if="loading" class="loading">加载中…</div>
      <div v-else-if="!items.length" class="empty">没有匹配的集群</div>
      <template v-else>
        <!-- 表头即排序控件：可排序的三列是页面上真正需要「比大小」的列。
             用 <button> 而不是给 <span> 挂 @click —— 键盘能 Tab 到、回车能触发，
             读屏也会念成按钮；配合 aria-sort 说明当前按哪列、什么方向。 -->
        <div class="register-head reg-cluster">
          <span v-for="cell in HEAD_CELLS" :key="cell.key" :class="[cell.area, { 'cell-right': cell.right }]">
            <button
              v-if="cell.sort"
              type="button"
              class="th-sort"
              :class="{ active: filters.sort === cell.sort }"
              :aria-sort="ariaSort(cell)"
              :title="sortTitle(cell)"
              @click="sortBy(cell.sort)"
            >
              {{ cell.label }}
              <span class="arrow" aria-hidden="true">{{ filters.sort === cell.sort ? cell.arrow : '↕' }}</span>
            </button>
            <template v-else>{{ cell.label }}</template>
          </span>
        </div>

        <div class="register enter">
          <ClusterRow v-for="item in items" :key="item.slug" :item="item" />
        </div>
      </template>

      <Pager :meta="meta" @change="setPage" />
    </section>
  </div>
</template>

<style scoped>
.filters {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  padding: 12px;
}

.filters input[type='search'] {
  flex: 1 1 220px;
}
</style>