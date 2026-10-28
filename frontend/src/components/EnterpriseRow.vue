<script setup>
/**
 * 登记册的一行：字段按列对齐，可横向比对。
 *
 * 为什么不是卡片：本站的企业名录动辄上千家，采购商要的是**比价**
 * （哪家在哪、主营什么、有没有电话）。卡片网格每张只装一家、字段位置随
 * 内容长短浮动，扫不动也对不齐。细线行 + 固定列才是这种数据该有的形制。
 *
 * 与 EnterpriseCard 的分工：
 *   - 名录页、集群页（大批量）用本组件（register-row.enterprise）
 *   - 收藏页那种「少量、要突出单条」的场景仍可用 EnterpriseCard
 */
import GradeTag from './GradeTag.vue'
import FavoriteButton from './FavoriteButton.vue'

defineProps({
  item: { type: Object, required: true },
})
</script>

<template>
  <div class="register-row reg-enterprise">
    <div class="c-name">
      <RouterLink class="ent-name" :to="{ name: 'enterprise', params: { id: item.id } }">
        {{ item.name }}
      </RouterLink>
      <div class="tiny muted" v-if="item.attribution_note">{{ item.attribution_note }}</div>
    </div>

    <div class="c-region small muted">
      {{ [item.city, item.district, item.town].filter(Boolean).join(' · ') || '所属地区待补' }}
      <span v-if="item.region_source === 'name_prefix'" class="tiny">（按名称推断）</span>
      <span v-else-if="item.region_source === 'out_of_province'" class="tiny">（省外产区）</span>
      <!-- 「对标龙头」不是这个产区的企业，但**在浙江**（天堂伞在萧山、均胜在宁波）：
           标成省外就把它从浙江统计里划掉了，所以两种情形分开标。 -->
      <span v-else-if="item.region_source === 'benchmark'" class="tiny">（对标龙头 · 非本产区）</span>
    </div>

    <div class="c-cluster small muted">
      <!-- 集群放次要行；产品与集群都可能有 2 行以上，所以各自截断并带 title -->
      <span class="clamp-2" :title="item.cluster_names?.join('、') || ''">
        {{ item.cluster_names?.join('、') || '—' }}
      </span>
    </div>

    <div class="c-product small">
      <!-- 产品是采购商最常扫的一列，所以它**不做灰化**（其余次要字段才是 muted）：
           一行里最深的信息应该是「这家做什么」。 -->
      <span v-if="item.products?.length" class="clamp-2" :title="item.products.join('；')">
        {{ item.products.join('；') }}
      </span>
      <span v-else class="muted">未登记产品</span>
    </div>

    <div class="c-contact cell-right">
      <div class="stack tiny contact-tags">
        <span v-if="item.website" class="tag tag-plain">官网</span>
        <span v-if="item.public_phone" class="tag tag-plain mono">{{ item.public_phone }}</span>
        <span v-if="item.public_email" class="tag tag-plain">邮箱</span>
        <span v-if="item.contact_status === 'empty'" class="tag tag-stub">联系方式待补</span>
        <span v-else-if="item.contact_status === 'partial'" class="tag tag-stub">不完整</span>
      </div>
      <div class="stack tiny contact-tags" style="margin-top: 4px">
        <GradeTag :grade="item.data_grade" :label="item.data_grade_label" />
        <FavoriteButton :enterprise-id="item.id" size="small" />
      </div>
    </div>
  </div>
</template>