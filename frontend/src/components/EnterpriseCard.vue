<script setup>
import { GRADE_CLASS } from '../api'
import FavoriteButton from './FavoriteButton.vue'

defineProps({
  item: { type: Object, required: true },
})
</script>

<template>
  <RouterLink class="card ent" :to="{ name: 'enterprise', params: { id: item.id } }">
    <div class="row">
      <strong class="name">{{ item.name }}</strong>
      <span class="tag" :class="GRADE_CLASS[item.data_grade] || 'tag-plain'">
        {{ item.data_grade_label }}
      </span>
    </div>
    <div class="small muted mono">
      {{ [item.city, item.district, item.town].filter(Boolean).join(' · ') || '所属地区待补' }}
      <span v-if="item.region_source === 'name_prefix'" class="tag tag-plain tiny" style="margin-left: 6px">
        地区按企业名称前缀推断
      </span>
      <!-- 省外企业必须在**卡片上**就看得出来：集群页会把它们和浙江企业混在一个列表里，
           不标就会被读成「这个浙江集群有这么多家」。企业详情页已有同样的标签，
           但读者是先扫列表的。 -->
      <span v-else-if="item.region_source === 'out_of_province'" class="tag tag-stub tiny" style="margin-left: 6px">
        省外产区
      </span>
      <!-- 「对标龙头」在浙江境内（天堂伞在萧山、均胜在宁波）：不能标省外，
           否则读者会以为浙江的龙头企业是外省公司，统计上也会被划出浙江。 -->
      <span v-else-if="item.region_source === 'benchmark'" class="tag tag-stub tiny" style="margin-left: 6px">
        对标龙头 · 非本产区
      </span>
    </div>
    <div class="stack tiny" v-if="item.cluster_names?.length">
      <span v-for="name in item.cluster_names" :key="name" class="tag tag-plain">{{ name }}</span>
    </div>
    <p class="small products line-clamp" v-if="item.products?.length">
      {{ item.products.join('；') }}
    </p>
    <div class="stack tiny contact">
      <span v-if="item.website" class="tag tag-plain">官网</span>
      <span v-if="item.public_phone" class="tag tag-plain mono">{{ item.public_phone }}</span>
      <span v-if="item.public_email" class="tag tag-plain">{{ item.public_email }}</span>
      <span v-if="item.contact_status === 'empty'" class="tag tag-stub">联系方式待补</span>
      <span v-else-if="item.contact_status === 'partial'" class="tag tag-stub">联系方式不完整</span>
    </div>
    <div v-if="item.attribution_note" class="tiny muted">{{ item.attribution_note }}</div>
    <div
      v-if="item.contact_sources && item.contact_sources.website"
      class="tiny muted"
    >
      来源：{{ item.contact_sources.website }}
    </div>
    <div class="card-foot">
      <FavoriteButton :enterprise-id="item.id" size="small" />
    </div>
  </RouterLink>
</template>

<style scoped>
.ent {
  display: block;
  padding: 14px 16px;
  transition: border-color 0.15s ease;
}

.ent:hover {
  border-color: #c9ccd3;
  color: inherit;
}

.name {
  font-size: 15px;
  line-height: 1.45;
}

.products {
  margin: 8px 0 6px;
  color: #3c4149;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
}

.contact {
  margin-top: 6px;
}

.card-foot {
  margin-top: 9px;
  padding-top: 8px;
  border-top: 1px dashed var(--border);
}
</style>