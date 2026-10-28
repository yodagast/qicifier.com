<script setup>
import { computed, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { api } from '../api'
import { applySeo, articleJsonLd } from '../seo'
import { buildEmbeddedDoc, hasHeading } from '../researchDoc'

// 一篇行业研究：正文**嵌在站点外壳里**（页头、页脚、导航都在），不是跳到一份
// 独立的 HTML 文档。文档自带的样式由 researchDoc.js 加作用域前缀后注入，
// 只影响下面那个 .research-doc 容器，不会污染全站排版。
const route = useRoute()
const article = ref(null)
const embedded = ref('')
const loading = ref(false)
const error = ref('')

const fallbackHead = computed(() => article.value && !hasHeading(article.value.body))

async function load(slug) {
  loading.value = true
  error.value = ''
  article.value = null
  embedded.value = ''
  try {
    const payload = await api.research(slug)
    article.value = payload
    embedded.value = buildEmbeddedDoc(payload)
    applySeo({
      title: payload.article.title,
      description: payload.article.summary,
      path: `/research/${encodeURIComponent(slug)}`,
      jsonLd: articleJsonLd(payload.article),
    })
  } catch (err) {
    error.value = String(err.message || err)
  } finally {
    loading.value = false
  }
}

watch(() => route.params.slug, (slug) => slug && load(slug), { immediate: true })
</script>

<template>
  <div class="container">
    <p class="research-back">
      <RouterLink :to="{ name: 'research' }">← 行业研究</RouterLink>
    </p>

    <div v-if="loading" class="loading">加载中…</div>
    <p v-else-if="error" class="notice warn">加载失败：{{ error }}</p>

    <template v-else-if="article">
      <!-- 文档通常自带 h1（它的 hero 区）；只有缺标题时才在外面补一个，
           避免同一页出现两个 h1。日期条是站点的，因为它不属于文档内容。 -->
      <div v-if="fallbackHead" class="page-head">
        <h1>{{ article.article.title }}</h1>
        <p class="lead">{{ article.article.summary }}</p>
      </div>
      <p v-else class="research-meta">
        <time :datetime="article.article.published_at">{{ article.article.published_at }}</time>
        <span class="research-meta-sep">·</span>
        <span>站方整理</span>
      </p>

      <article class="research-shell">
        <div class="research-doc" v-html="embedded"></div>
      </article>
    </template>
  </div>
</template>

<style scoped>
.research-back {
  margin: 22px 0 0;
  font-size: var(--fs-sm);
}

.research-meta {
  margin: 22px 0 10px;
  font-size: 12px;
  color: var(--ink-3);
  font-variant-numeric: tabular-nums;
}

.research-meta-sep {
  margin: 0 6px;
}

/* 文档自带背景与内边距，这里只给一条边界把一个「外来的文档」框在站点里，
   不再叠站点自己的卡片阴影 —— 两层边框会让它看起来像套娃。 */
.research-shell {
  margin: 12px 0 40px;
  border: 1px solid var(--rule-strong);
  border-radius: var(--r-md);
  overflow: hidden;
  background: var(--panel);
}

.research-doc {
  /* 文档自己的 CSS 全部挂在 .research-doc 之下，这里只需要把它重置回正文排版 */
  font-size: var(--fs-base);
  line-height: 1.75;
  color: var(--ink);
  overflow-wrap: break-word;
}

/* 文档里的锚点（目录胶囊）跳转时，站点吸顶的页头会盖住标题 ——
   给所有带 id 的锚点留出页头的高度。 */
.research-doc :deep([id]) {
  scroll-margin-top: 88px;
}

/* 文档里的表格在窄屏会撑破容器：外框滚动，速度比「挤成豆腐块」好 */
.research-doc :deep(table) {
  display: block;
  max-width: 100%;
  overflow-x: auto;
}

.research-doc :deep(img),
.research-doc :deep(svg) {
  max-width: 100%;
  height: auto;
}
</style>
