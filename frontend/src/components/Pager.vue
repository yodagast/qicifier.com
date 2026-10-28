<script setup>
const props = defineProps({
  meta: { type: Object, required: true },
})

const emit = defineEmits(['change'])

function go(page) {
  if (page < 1 || page > props.meta.pages || page === props.meta.page) return
  emit('change', page)
}
</script>

<template>
  <div v-if="meta.pages > 1" class="pager">
    <button :disabled="meta.page <= 1" @click="go(meta.page - 1)">上一页</button>
    <span class="mono">第 {{ meta.page }} / {{ meta.pages }} 页 · 共 {{ meta.total }} 条</span>
    <button :disabled="meta.page >= meta.pages" @click="go(meta.page + 1)">下一页</button>
  </div>
</template>