<script setup>
import { computed } from 'vue'
import { GRADE_CLASS } from '../api'

const props = defineProps({
  grade: { type: String, default: '' },
  label: { type: String, default: '' },
  stub: { type: Boolean, default: false },
  stubText: { type: String, default: '企业不足 10 家' },
})

const cls = computed(() => GRADE_CLASS[props.grade] || 'tag-plain')
</script>

<template>
  <span style="display: inline-flex; gap: 4px; align-items: center; flex-wrap: wrap">
    <span v-if="label" class="tag" :class="cls">{{ label }}</span>
    <span
      v-if="stub"
      class="tag tag-stub"
      :title="`按收录判据③：集群在企业库中匹配到的企业不足 10 家，页面为「空壳集群」状态，需先补企业再建档案`"
    >
      {{ stubText }}
    </span>
  </span>
</template>