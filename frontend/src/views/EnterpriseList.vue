<script setup>
import { onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { api } from '../api'
import EnterpriseRow from '../components/EnterpriseRow.vue'
import Pager from '../components/Pager.vue'

const route = useRoute()
const router = useRouter()

const items = ref([])
const meta = ref({ total: 0, page: 1, page_size: 20, pages: 0 })
const loading = ref(false)
const error = ref('')
const towns = ref([])
const gradeOptions = ref([])

const filters = ref({
  q: '',
  product: '',
  city: '',
  district: '',
  town: '',
  data_grade: '',
  scope: 'all',
  contact_status: '',
  has_contact: false,
  sort: 'relevance',
  page: 1,
})

function fromQuery() {
  filters.value = {
    q: route.query.q || '',
    product: route.query.product || '',
    city: route.query.city || '',
    district: route.query.district || '',
    town: route.query.town || '',
    data_grade: route.query.data_grade || '',
    scope: route.query.scope || 'all',
    contact_status: route.query.contact_status || '',
    has_contact: route.query.has_contact === 'true',
    sort: route.query.sort || 'relevance',
    page: Number(route.query.page || 1),
  }
}

async function load() {
  loading.value = true
  error.value = ''
  try {
    const data = await api.enterprises({ ...filters.value, page_size: 24 })
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
    if (value === '' || value === false) continue
    if (key === 'page' && value === 1) continue
    if (key === 'sort' && value === 'relevance') continue
    if (key === 'scope' && value === 'all') continue
    query[key] = String(value)
  }
  router.replace({ name: 'enterprises', query })
}

function setPage(page) {
  filters.value.page = page
  sync()
}

watch(
  () => route.query,
  () => {
    fromQuery()
    load()
  },
  { immediate: true },
)

onMounted(async () => {
  try {
    const [tree, sources] = await Promise.all([api.regions(), api.sources()])
    const list = []
    for (const city of tree[0]?.children || []) {
      for (const district of city.children || []) {
        for (const town of district.children || []) {
          if (town.has_industry) {
            list.push({
              code: town.code,
              label: `${city.name} / ${district.name} / ${town.name}`,
              town: town.name,
              district: district.name,
              city: city.name,
            })
          }
        }
      }
    }
    towns.value = list
    gradeOptions.value = sources.grades || []
  } catch {
    /* 忽略筛选选项加载失败 */
  }
})

function pickTown(event) {
  const value = event.target.value
  const found = towns.value.find((t) => t.code === value)
  filters.value.town = found ? found.town : ''
  filters.value.district = found ? found.district : filters.value.district
  filters.value.city = found ? found.city : filters.value.city
  apply()
}
</script>

<template>
  <div class="container">
    <div class="page-head">
      <h1>企业名录</h1>
      <p class="lead">
        按产品、集群、镇街、核实程度四个入口检索。排序只依据公开可核验程度与资料完整度，对所有企业一致；不收入驻费、不卖排名。
      </p>
    </div>

    <section class="section">
      <div class="card filters">
        <input v-model="filters.q" type="search" placeholder="企业名 / 主营 / 地址" @keyup.enter="apply" />
        <input v-model="filters.product" type="text" placeholder="产品关键词，如 轴承 / 面料 / 锂电池" @keyup.enter="apply" />
        <select v-model="filters.city" @change="apply">
          <option value="">全部设区市</option>
          <option
            v-for="city in ['杭州市','宁波市','温州市','嘉兴市','湖州市','绍兴市','金华市','衢州市','舟山市','台州市','丽水市']"
            :key="city"
            :value="city"
          >
            {{ city }}
          </option>
        </select>
        <select :value="towns.find((t) => t.town === filters.town)?.code || ''" @change="pickTown">
          <option value="">全部镇街</option>
          <option v-for="town in towns" :key="town.code" :value="town.code">{{ town.label }}</option>
        </select>
        <select v-model="filters.scope" @change="apply" title="本库以浙江为主，另收录少量省外对比产区">
          <option value="all">产地范围：全部</option>
          <option value="zhejiang">只看浙江</option>
          <option value="out_of_province">只看省外对比产区</option>
        </select>
        <select v-model="filters.data_grade" @change="apply">
          <option value="">全部核实程度</option>
          <option v-for="grade in gradeOptions" :key="grade.key" :value="grade.key">
            {{ grade.label }}
          </option>
        </select>
        <select v-model="filters.contact_status" @change="apply">
          <option value="">联系方式不限</option>
          <option value="full">官网+联系方式齐全</option>
          <option value="partial">联系方式不完整</option>
          <option value="empty">联系方式待补</option>
        </select>
        <select v-model="filters.sort" @change="apply">
          <option value="relevance">按资料完整度</option>
          <option value="name">按名称</option>
        </select>
        <label class="checkline tiny muted">
          <input v-model="filters.has_contact" type="checkbox" @change="apply" />
          只看已核实官网或电话
        </label>
        <button class="primary" @click="apply">筛选</button>
      </div>

      <p v-if="error" class="notice warn">接口异常：{{ error }}</p>
      <div class="row" style="margin-top: 18px">
        <span class="small muted mono">共 {{ meta.total }} 家企业</span>
        <RouterLink class="small" to="/about">核实程度说明 →</RouterLink>
      </div>

      <div v-if="loading" class="loading">加载中…</div>
      <div v-else-if="!items.length" class="empty">没有匹配的企业</div>
      <template v-else>
        <div class="register-head reg-enterprise">
          <span class="c-name">企业</span>
          <span class="c-region">所属地区</span>
          <span class="c-cluster">集群</span>
          <span class="c-product">主营产品</span>
          <span class="c-contact cell-right">联系方式 / 核实程度</span>
        </div>
        <div class="register enter">
          <EnterpriseRow v-for="item in items" :key="item.id" :item="item" />
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
  align-items: center;
}

.filters input[type='search'] {
  flex: 1 1 200px;
}

.filters input[type='text'] {
  flex: 1 1 200px;
}

.checkline {
  display: inline-flex;
  align-items: center;
  gap: 5px;
}
</style>