<script setup>
defineProps({
  label: { type: String, default: '' },
  title: { type: String, default: '' },
})
</script>

<template>
  <span v-if="label" class="tag tag-plain tiny" :title="title">{{ label }}</span>
</template>