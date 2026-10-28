<script setup>
import { computed, onMounted, ref } from 'vue'
import { api } from '../api'
import GradeTag from '../components/GradeTag.vue'

const tree = ref([])
const selected = ref(null)
const clusters = ref([])
const loading = ref(false)
const error = ref('')
const clusterFilter = ref('all')

const province = computed(() => tree.value[0] || null)
const cities = computed(() => province.value?.children || [])

// 站点收录总数（与首页同一个数）。此前这里第一格放的是 `province.enterprise_count`
// —— 那是「按县汇总的家数」，地盘上点得到的才有，比收录总数小一截；
// 它占据总量位会让读者以为企业少了一批，所以这一格改放收录总数。
// 差额（名单没给地址、企业名里也不含地名，落不到县一级的那部分）只写在
// 「口径与边界」里，不在数字带上重复 —— 那一行解释对读者没用，反而让第一格
// 变成「一句话带三个数字」。口径本身没变，可核对的算式仍是 2626 = 2512 + 114。
const stats = ref(null)
const zhejiang = computed(() =>
  stats.value ? stats.value.enterprise_total - stats.value.enterprise_out_of_province : 0,
)

// 省级新区/开发区单独计数：它们不是国标县级行政区，不能并进「90 个县级行政区」
const countyCount = computed(() =>
  cities.value.reduce((sum, c) => sum + (c.children || []).filter((d) => !d.is_zone).length, 0),
)
const zoneCount = computed(() =>
  cities.value.reduce((sum, c) => sum + (c.children || []).filter((d) => d.is_zone).length, 0),
)

const townOptions = computed(() => {
  const result = []
  for (const city of cities.value) {
    for (const district of city.children || []) {
      for (const town of district.children || []) {
        if (town.has_industry) {
          result.push({ ...town, city: city.name, district: district.name })
        }
      }
    }
  }
  return result
})

const visibleClusters = computed(() => {
  const items = clusters.value
  if (clusterFilter.value === 'official') return items.filter((c) => c.data_grade === 'official')
  if (clusterFilter.value === 'curated') return items.filter((c) => c.data_grade !== 'official')
  return items
})

async function pick(node, cityName, districtName) {
  selected.value = { ...node, cityLabel: cityName, districtLabel: districtName }
  loading.value = true
  error.value = ''
  try {
    const params = node.level === 'town' ? { town: node.name } : { district: node.name }
    const data = await api.clusters({ ...params, page_size: 50 })
    clusters.value = data.items
  } catch (err) {
    error.value = String(err.message || err)
  } finally {
    loading.value = false
  }
}

onMounted(async () => {
  try {
    const [regions, statsData] = await Promise.all([api.regions(), api.stats()])
    tree.value = regions
    stats.value = statsData
    const first = cities.value.find((c) => c.children?.length)
    if (first) {
      const district = first.children.find((d) => d.has_industry) || first.children[0]
      if (district) await pick(district, first.name, '')
    }
  } catch (err) {
    error.value = String(err.message || err)
  }
})
</script>

<template>
  <div class="container">
    <div class="page-head">
      <h1>产业地图</h1>
      <p class="lead">
        浙江 11 个设区市 · 90 个县级行政区；有产业的镇/街道单独成一级实体。点选县级行政区或镇街，查看落在该地的集群档案。
      </p>
    </div>

    <section class="section" v-if="province">
      <p v-if="error" class="notice warn">接口异常：{{ error }}</p>

      <div class="kpi-band">
        <div class="kpi-cell lead-cell">
          <div class="value">{{ stats ? stats.enterprise_total : '—' }}</div>
          <div class="label">
            已收录企业<br />
            <template v-if="stats">
              浙江 {{ zhejiang }} · 省外对比 {{ stats.enterprise_out_of_province }}
            </template>
          </div>
        </div>
        <div class="kpi-cell">
          <div class="value">{{ province.children.length }}</div>
          <div class="label">设区市</div>
        </div>
        <div class="kpi-cell">
          <div class="value">{{ countyCount }}</div>
          <div class="label">
            县级行政区<template v-if="zoneCount"><br />另有 {{ zoneCount }} 个省级新区/开发区</template>
          </div>
        </div>
        <div class="kpi-cell">
          <div class="value">{{ townOptions.length }}</div>
          <div class="label">有产业的镇/街道</div>
        </div>
      </div>
    </section>

    <section class="section" v-if="cities.length">
      <h2>按设区市 / 县（市、区）/ 镇街展开</h2>

      <div class="city-list">
        <div v-for="city in cities" :key="city.code" class="city">
          <div class="row city-head">
            <strong>{{ city.name }}</strong>
            <span class="small muted mono">
              {{ (city.children || []).filter((d) => !d.is_zone).length }} 县 · {{ city.enterprise_count }} 家
            </span>
          </div>
          <div class="stack">
            <button
              v-for="district in city.children"
              :key="district.code"
              class="chip"
              :class="{ active: selected?.code === district.code, empty: !district.has_industry, zone: district.is_zone }"
              :title="district.is_zone ? '省级新区/开发区（非国标县级行政区）' : ''"
              @click="pick(district, city.name, '')"
            >
              {{ district.name }}
              <span v-if="district.is_zone" class="tiny">·新区</span>
              <span class="mono tiny" v-if="district.enterprise_count">({{ district.enterprise_count }})</span>
            </button>
          </div>
          <div class="stack town-row" v-if="(city.children || []).some((d) => (d.children || []).some((t) => t.has_industry))">
            <span class="tiny muted">镇街：</span>
            <template v-for="district in city.children" :key="`t-${district.code}`">
              <button
                v-for="town in (district.children || []).filter((t) => t.has_industry)"
                :key="town.code"
                class="chip town"
                :class="{ active: selected?.code === town.code }"
                @click="pick(town, city.name, district.name)"
              >
                {{ district.name }} / {{ town.name }}
              </button>
            </template>
          </div>
        </div>
      </div>
    </section>

    <section class="section" v-if="selected">
      <div class="row">
        <h2>{{ [selected.cityLabel, selected.districtLabel, selected.name].filter(Boolean).join(' · ') }}</h2>
        <div class="stack">
          <select v-model="clusterFilter">
            <option value="all">全部档位</option>
            <option value="official">只看官方认定</option>
            <option value="curated">只看站方整理</option>
          </select>
        </div>
      </div>
      <p class="hint">
        该地区关联集群 {{ visibleClusters.length }} 个。集群与行政区是解耦的，一个集群可能横跨多个县。
      </p>

      <div v-if="loading" class="loading">加载中…</div>
      <div v-else-if="!visibleClusters.length" class="empty">该地区暂无关联集群档案</div>
      <div v-else class="grid cols-2">
        <RouterLink
          v-for="cluster in visibleClusters"
          :key="cluster.slug"
          class="card row-item"
          :to="{ name: 'cluster', params: { slug: cluster.slug } }"
        >
          <div class="row">
            <strong>{{ cluster.name }}</strong>
            <GradeTag
              :grade="cluster.data_grade"
              :label="cluster.data_grade_label"
              :stub="cluster.is_stub"
              :stub-text="cluster.enterprise_count === 0 ? '暂无挂载企业' : `仅 ${cluster.enterprise_count} 家`"
            />
          </div>
          <div class="small mono muted">{{ cluster.industry_l1 }} {{ cluster.industry_l2 }}</div>
          <div class="small"><span class="mono">{{ cluster.enterprise_count }}</span> 家已收录企业</div>
        </RouterLink>
      </div>
    </section>
  </div>
</template>

<style scoped>
.city-list {
  display: grid;
  gap: 12px;
}

.city {
  background: var(--panel);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  padding: 12px 14px;
}

.city-head {
  margin-bottom: 8px;
}

.chip {
  padding: 4px 10px;
  border-radius: 999px;
  font-size: 13px;
  background: #fff;
  border: 1px solid var(--border);
}

.chip:hover {
  border-color: var(--accent);
}

.chip.active {
  background: var(--brand-soft);
  border-color: var(--brand);
  color: var(--brand);
  font-weight: 600;
}

.chip.empty {
  color: #9aa0a6;
}

.chip.zone {
  border-style: dashed;
}

.chip.town {
  font-size: 12px;
  background: #fafbfc;
}

.town-row {
  margin-top: 8px;
  padding-top: 8px;
  border-top: 1px dashed var(--border);
}

.row-item {
  display: block;
  padding: 13px 15px;
}

.row-item:hover {
  border-color: #c9ccd3;
  color: inherit;
}
</style>