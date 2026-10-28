<script setup>
import { computed, ref, watch } from 'vue'
import { isFavorited, isLoggedIn, session, toggleFavorite } from '../session'

const props = defineProps({
  enterpriseId: { type: Number, required: true },
  size: { type: String, default: 'normal' },
})

const busy = ref(false)
const error = ref('')
const favorited = ref(isFavorited(props.enterpriseId))

watch(
  () => props.enterpriseId,
  (id) => {
    favorited.value = isFavorited(id)
  },
)
watch(isLoggedIn, () => {
  favorited.value = isFavorited(props.enterpriseId)
})

// 关键：监听**收藏集合本身**。
//
// 恢复登录态是异步的，而且顺序是「先 set user，再拉收藏 id」——
// 只监听 isLoggedIn 的话，回调会在 favoriteIds 到位**之前**跑完，
// 之后再没有任何东西通知这个按钮。结果是刷新页面后，已收藏的企业显示成「☆ 收藏」，
// 用户点一下反而**取消**了收藏（而且看起来「什么都没发生」）。
watch(
  () => session.favoriteIds,
  () => {
    favorited.value = isFavorited(props.enterpriseId)
  },
)

const label = computed(() => {
  if (!isLoggedIn.value) return '登录后收藏'
  return favorited.value ? '已收藏' : '收藏'
})

async function onClick() {
  if (!isLoggedIn.value) {
    error.value = '请先登录再收藏'
    return
  }
  busy.value = true
  error.value = ''
  try {
    favorited.value = await toggleFavorite(props.enterpriseId)
  } catch (err) {
    error.value = String(err.message || err)
  } finally {
    busy.value = false
  }
}
</script>

<template>
  <div class="fav-wrap">
    <button
      class="fav"
      :class="{ on: favorited, small: size === 'small' }"
      :disabled="busy"
      :title="isLoggedIn ? (favorited ? '取消收藏' : '收藏这家工厂') : '请先登录'"
      @click.stop.prevent="onClick"
    >
      <span class="star">{{ favorited ? '★' : '☆' }}</span>
      {{ label }}
    </button>
    <span v-if="error" class="tiny err">{{ error }}</span>
  </div>
</template>

<style scoped>
.fav-wrap {
  display: inline-flex;
  align-items: center;
  gap: 6px;
}

.fav {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 4px 10px;
  border-radius: 999px;
  font-size: 12px;
  background: #fff;
  border: 1px solid var(--border);
  color: var(--muted);
  white-space: nowrap;
}

.fav:hover:not(:disabled) {
  border-color: var(--brand);
  color: var(--brand);
}

.fav.on {
  background: #fff7e6;
  border-color: #f0c36d;
  color: #92600a;
}

.fav .star {
  font-size: 13px;
  line-height: 1;
}

.err {
  color: var(--brand);
}
</style>