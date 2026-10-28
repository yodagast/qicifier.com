<script setup>
import { onMounted, ref, watch } from 'vue'
import { api, GRADE_CLASS } from '../api'
import { isLoggedIn, session } from '../session'

const items = ref([])
// 收藏了、但企业已经不在最新名单里的记录（名单修订后可能被移除）
const stale = ref([])
const loading = ref(true)
const loaded = ref(false)
const error = ref('')
const message = ref('')
const savingId = ref(0)

async function load() {
  loading.value = true
  error.value = ''
  try {
    const data = await api.favorites()
    items.value = data.items
    stale.value = data.stale || []
    session.favoriteIds = new Set(data.items.map((row) => row.enterprise.id))
    if (session.user) {
      session.user.favorite_count = data.items.length
      session.user.stale_count = stale.value.length
    }
  } catch (err) {
    error.value = String(err.message || err)
  } finally {
    loading.value = false
    loaded.value = true
  }
}

// 直接调删除接口，不用 toggleFavorite：toggle 依赖本地 favoriteIds 是否同步，
// 而这里要的是「无论本地状态如何，都把它删掉」。
async function remove(id) {
  savingId.value = id
  const removedName = items.value.find((row) => row.enterprise.id === id)?.enterprise.name || ''
  try {
    await api.removeFavorite(id)
    items.value = items.value.filter((row) => row.enterprise.id !== id)
    session.favoriteIds.delete(id)
    session.favoriteIds = new Set(session.favoriteIds)
    if (session.user) session.user.favorite_count = Math.max(0, session.user.favorite_count - 1)
    message.value = `已取消收藏「${removedName}」`
  } catch (err) {
    error.value = String(err.message || err)
  } finally {
    savingId.value = 0
  }
}

async function purgeStale() {
  savingId.value = -1
  try {
    const result = await api.purgeStaleFavorites()
    stale.value = []
    if (session.user) session.user.stale_count = 0
    message.value = `已清理 ${result.count} 条失效收藏`
  } catch (err) {
    error.value = String(err.message || err)
  } finally {
    savingId.value = 0
  }
}

async function saveNote(row) {
  savingId.value = row.enterprise.id
  message.value = ''
  try {
    await api.updateFavoriteNote(row.enterprise.id, row.note)
    // 保存成功要给个回执：这个按钮没有视觉状态变化，不给反馈用户会以为没生效、
    // 反复点，甚至怀疑是「登录坏了」
    message.value = `「${row.enterprise.name}」的备注已保存`
  } catch (err) {
    error.value = String(err.message || err)
  } finally {
    savingId.value = 0
  }
}

function contactLine(ent) {
  return [ent.website, ent.public_phone, ent.public_email].filter(Boolean).join(' · ')
}

// 刷新页面时 main.js 的 restore() 是异步的：必须等它落地（session.ready）
// 且确认已登录后再拉收藏，否则会先拿到 401 而误显示「请先登录」。
watch(
  [() => session.ready, () => isLoggedIn.value],
  ([ready, loggedIn]) => {
    if (ready && loggedIn && !loaded.value) load()
  },
  { immediate: true },
)

onMounted(() => {
  if (session.ready && !isLoggedIn.value) loading.value = false
})
</script>

<template>
  <div class="container">
    <div class="page-head">
      <h1>我的收藏</h1>
      <p class="lead">
        收藏是你个人的采购备忘，只有你自己能看到。备注可随手记下交期、起订量、报价等跟进信息。
      </p>
    </div>

    <section class="section">

      <p v-if="error" class="notice warn">{{ error }}</p>
      <p v-if="message" class="notice">{{ message }}</p>

      <div v-if="stale.length" class="notice warn" style="margin-bottom: 14px">
        有 {{ stale.length }} 条收藏对应的企业已不在最新名单里（名单修订时可能被移除），
        这里不展示它们，也不会替你悄悄删掉：
        <span class="mono">{{ stale.map((row) => row.name).join('、') }}</span>
        <button class="tiny-btn" style="margin-left: 8px" :disabled="savingId === -1" @click="purgeStale">
          清理这些记录
        </button>
      </div>

      <div v-if="!isLoggedIn" class="empty">
        请先<RouterLink :to="{ name: 'login', query: { redirect: '/favorites' } }">登录</RouterLink>后查看收藏。
      </div>
      <div v-else-if="loading" class="loading">加载中…</div>
      <div v-else-if="!items.length" class="empty">
        还没有收藏任何工厂。在<RouterLink :to="{ name: 'enterprises' }">企业名录</RouterLink>里点「☆ 收藏」即可加入。
      </div>

      <div v-else class="list">
        <div v-for="row in items" :key="row.enterprise.id" class="card item">
          <div class="row">
            <RouterLink class="name" :to="{ name: 'enterprise', params: { id: row.enterprise.id } }">
              {{ row.enterprise.name }}
            </RouterLink>
            <span class="tag" :class="GRADE_CLASS[row.enterprise.data_grade] || 'tag-plain'">
              {{ row.enterprise.data_grade_label }}
            </span>
          </div>

          <div class="small muted mono">
            {{ [row.enterprise.city, row.enterprise.district, row.enterprise.town].filter(Boolean).join(' · ') || '地区待补' }}
          </div>

          <div v-if="row.enterprise.cluster_names?.length" class="stack tiny">
            <span v-for="name in row.enterprise.cluster_names" :key="name" class="tag tag-plain">{{ name }}</span>
          </div>

          <div class="small mono contact">
            <template v-if="row.enterprise.website">
              官网：<a :href="row.enterprise.website" target="_blank" rel="noopener noreferrer">{{ row.enterprise.website }}</a>
            </template>
            <template v-if="row.enterprise.public_phone"> · 电话：{{ row.enterprise.public_phone }}</template>
            <template v-if="row.enterprise.public_email"> · 邮箱：{{ row.enterprise.public_email }}</template>
            <span v-if="!contactLine(row.enterprise)" class="muted">联系方式待补</span>
          </div>

          <div class="note-row">
            <input v-model="row.note" type="text" placeholder="备注：交期 / 起订量 / 报价 / 跟进状态" />
            <button :disabled="savingId === row.enterprise.id" @click="saveNote(row)">保存备注</button>
            <button class="danger" :disabled="savingId === row.enterprise.id" @click="remove(row.enterprise.id)">
              取消收藏
            </button>
          </div>

          <div class="tiny muted">收藏于 {{ String(row.created_at).slice(0, 10) }}</div>
        </div>
      </div>
    </section>
  </div>
</template>

<style scoped>
.list {
  display: grid;
  gap: 12px;
}

.item {
  padding: 15px 16px;
}

.name {
  font-weight: 600;
  font-size: 15px;
}

.contact {
  margin-top: 6px;
}

.note-row {
  display: flex;
  gap: 8px;
  margin: 10px 0 6px;
}

.note-row input {
  flex: 1;
}

button.danger {
  color: var(--brand);
  border-color: #f0c7c9;
}

button.danger:hover:not(:disabled) {
  background: var(--brand-soft);
}
</style>