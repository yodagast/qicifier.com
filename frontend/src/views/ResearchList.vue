<script setup>
import { onMounted, ref } from 'vue'
import { api } from '../api'
import { applySeo } from '../seo'

// 行业研究 = 站方自己写的方法论与调研报告（后台导入的 HTML 文档）。
//
// 这一页只列「有哪些篇」，正文在 /research/:slug 里**嵌进站点外壳**显示 ——
// 不是跳到一份独立文档，读者不丢导航与页脚。
const items = ref([])
const loading = ref(true)
const error = ref('')

onMounted(async () => {
  try {
    const payload = await api.researchList()
    items.value = payload.items || []
  } catch (err) {
    error.value = String(err.message || err)
  } finally {
    loading.value = false
  }
})
</script>

<template>
  <div class="container">
    <div class="page-head">
      <h1>行业研究</h1>
    </div>

    <div v-if="loading" class="loading">加载中…</div>
    <p v-else-if="error" class="notice warn">加载失败：{{ error }}</p>
    <p v-else-if="!items.length" class="empty">暂无已发布的行业研究。</p>

    <ol v-else class="research-list">
      <li v-for="item in items" :key="item.slug" class="research-item">
        <RouterLink
          class="research-link serif"
          :to="{ name: 'research-article', params: { slug: item.slug } }"
        >
          {{ item.title }}
        </RouterLink>
        <p class="research-summary">{{ item.summary }}</p>
        <time class="research-date" :datetime="item.published_at">{{ item.published_at }}</time>
      </li>
    </ol>
  </div>
</template>

<style scoped>
.research-list {
  list-style: none;
  margin: 0;
  padding: 0;
  counter-reset: research;
}

/* 台账式列表：一条一篇，靠细线与序号分隔，不做卡片浮起 ——
   与集群 / 企业列表同一套形制（见 styles.css 的登记册风格）。 */
.research-item {
  position: relative;
  padding: 20px 0 20px 44px;
  border-top: 1px solid var(--rule);
  counter-increment: research;
}

.research-item:first-child {
  border-top: 1px solid var(--rule-strong);
}

.research-item::before {
  content: counter(research, decimal-leading-zero);
  position: absolute;
  left: 0;
  top: 22px;
  font-size: var(--fs-sm);
  color: var(--ink-3);
  font-variant-numeric: tabular-nums;
}

.research-link {
  font-size: var(--fs-md);
  font-weight: 600;
  color: var(--ink);
  text-decoration: none;
  border-bottom: 1px solid transparent;
}

.research-link:hover {
  color: var(--seal);
  border-bottom-color: var(--seal);
}

.research-summary {
  margin: 6px 0 6px;
  color: var(--ink-2);
  font-size: var(--fs-sm);
  max-width: 74ch;
}

.research-date {
  font-size: 12px;
  color: var(--ink-3);
  font-variant-numeric: tabular-nums;
}
</style>
