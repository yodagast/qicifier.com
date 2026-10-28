<script setup>
/**
 * 登记册的一行（集群）：与 EnterpriseRow 同一形制 —— 一张集群有很多属性
 * （产业、主产区、企业数、档位），放进卡片只会变成四行高度不一的文字。
 */
import GradeTag from './GradeTag.vue'

const props = defineProps({
  item: { type: Object, required: true },
})

// 「产业」串由后端给（`ClusterSummary.industry_label`）：列表**按同一串排序**，
// 前端再拼一遍就会出现「排序结果看着不像按产业排」这种只在一半数据上发生的问题。
const industry = props.item.industry_label || '—'
</script>

<template>
  <RouterLink class="register-row reg-cluster" :to="{ name: 'cluster', params: { slug: item.slug } }">
    <div class="c-name">
      <strong class="ent-name">{{ item.name }}</strong>
      <span v-if="item.aliases?.length" class="tiny muted">
        （{{ item.aliases.slice(0, 3).join('、') }}）
      </span>
    </div>

    <!-- 赛道集群的名字与 industry_l1 常常一字不差（「低空经济与航空航天」），
         照抄一遍只是把同一句话说两次。相同就整格不渲染 ——
         写「—」会被读成「这个字段我们不知道」，而实际是「这个字段对这类集群本就冗余」。 -->
    <div v-if="industry !== item.name" class="c-industry small muted">
      {{ industry }}
    </div>

    <!-- 415X 是**全省赛道**，本来就没有单一主产区；写「待补」是在承诺一件
         永远不会发生的事（我们不会给「低空经济与航空航天」编一个产地）。
         空就写空，并说清是哪种空。 -->
    <div class="c-region small muted">
      {{ [item.city, item.district, item.town].filter(Boolean).join(' · ') || '未标注产地' }}
    </div>

    <!-- 表头在窄档会收起来，所以计数自带单位，不依赖表头才读得懂。 -->
    <div class="c-count small mono muted cell-right">{{ item.enterprise_count }} 家</div>

    <div class="c-grade cell-right">
      <GradeTag
        :grade="item.data_grade"
        :label="item.data_grade_label"
        :stub="item.is_stub"
        :stub-text="item.enterprise_count === 0 ? '暂无挂载企业' : `仅 ${item.enterprise_count} 家`"
      />
    </div>
  </RouterLink>
</template>