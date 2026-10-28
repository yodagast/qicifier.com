<script setup>
import { computed, onMounted, ref } from 'vue'
import { api, GRADE_CLASS, GRADE_LABELS } from '../api'
import { aboutJsonLd, applySeo } from '../seo'

const payload = ref(null)
const stats = ref(null)
const error = ref('')

// 来源地图按**核实程度**分组：读者最关心的第一件事是「这份东西有多可信」，
// 而不是「它叫什么名字」。一份来源只属于一个档位，所以直接按 grade 归并。
const GROUP_ORDER = ['official', 'gov_open', 'media', 'site_curated', 'self_reported']

const sourceGroups = computed(() => {
  const sources = payload.value?.sources || []
  return GROUP_ORDER.map((grade) => ({
    grade,
    label: GRADE_LABELS[grade] || grade,
    sources: sources.filter((s) => s.grade === grade),
  })).filter((group) => group.sources.length)
})

onMounted(async () => {
  try {
    const [sources, statsData] = await Promise.all([api.sources(), api.stats()])
    payload.value = sources
    stats.value = statsData
    // 本页是全站唯一适合挂 Dataset 的地方（它讲的就是「这份数据是什么、由谁整理、
    // 怎么核实的」）；FAQPage 顺手带上，因为正文本身就是问答结构。
    applySeo({
      title: '数据来源与核实程度',
      description:
        '五级核实档位（官方认定 / 政府公开 / 媒体·协会 / 站方整理 / 企业自报）的判定口径，以及本站收录的每一份名单的来源披露。',
      path: '/about',
      jsonLd: aboutJsonLd(statsData),
    })
  } catch (err) {
    error.value = String(err.message || err)
  }
})
</script>

<template>
  <div class="container">
    <div class="page-head">
      <h1>数据来源与核实程度</h1>
      <p class="lead">
        分档标签是整套体系唯一的信任凭证，因此逐条标注在页面上，而不是整页一个标签。
      </p>
      <p v-if="error" class="notice warn">接口异常：{{ error }}</p>
    </div>

    <template v-if="payload">
      <section class="section">
        <h3>五档核实程度</h3>
        <div class="grid cols-2">
          <div v-for="grade in payload.grades" :key="grade.key" class="card panel">
            <div class="row">
              <strong>{{ grade.label }}</strong>
              <span class="tag" :class="GRADE_CLASS[grade.key] || 'tag-plain'">{{ grade.label }}</span>
            </div>
            <p class="small muted" style="margin: 6px 0 0">{{ grade.meaning }}</p>
          </div>
        </div>
      </section>

      <section class="section">
        <h3>来源地图</h3>
        <!-- 用「按核实程度分组的行」取代原来的四列表格：
             原表的「来源」列只有 50px 宽，中文名单名要折成 5 行，而「用于」列是
             上百字的说明 —— 两侧都读不下去。现在名称与档位在一行里读完，
             说明占满整行宽度，读者可以先扫档位、再决定要不要读细节。 -->
        <p class="hint">
          这一节回答「库里的每一条信息是从哪来的」。按<strong>核实程度</strong>分档列出：
          {{ sourceGroups.map((group) => group.label).join(' → ') }}。
          每条来源下面的小字是它在库里的用途，以及本站对它的处理方式（比如「官网只作候选，须先核验」）。
        </p>
        <div v-for="group in sourceGroups" :key="group.grade" class="source-group">
          <div class="source-group-head">
            <span class="tag" :class="GRADE_CLASS[group.grade] || 'tag-plain'">{{ group.label }}</span>
            <span class="tiny muted">{{ group.sources.length }} 份来源</span>
          </div>
          <div class="source-list">
            <div v-for="source in group.sources" :key="source.name" class="source-row">
              <div class="source-title">
                <strong>{{ source.name }}</strong>
                <span v-if="source.role" class="tag tag-plain">{{ source.role }}</span>
              </div>
              <div v-if="source.publisher" class="tiny muted">
                发布主体：{{ source.publisher }}
              </div>
              <p class="source-use">{{ source.used_for }}</p>
            </div>
          </div>
        </div>
      </section>

      <section class="section">
        <h3>口径与边界（不可省略）</h3>
        <ol class="boundary-list">
          <li v-for="(rule, index) in payload.boundaries" :key="index">{{ rule }}</li>
        </ol>
      </section>

      <section class="section" v-if="stats">
        <h3>名单口径声明</h3>
        <table class="card">
          <thead>
            <tr><th>名单</th><th>企业数</th><th>核实程度</th></tr>
          </thead>
          <tbody>
            <tr v-for="entry in stats.list_breakdown" :key="entry.name">
              <td>{{ entry.name }}</td>
              <td class="mono">{{ entry.count }}</td>
              <td>
                <!-- 档位必须跟着数据走。此前这里写死了 grade-official + 「官方认定」，
                     而实际 9 份名单里有 7 份是「站方整理」、1 份是「政府公开信息」——
                     等于把第三方调研清单说成官方认定，正是全站最不能出的错。 -->
                <span class="tag" :class="GRADE_CLASS[entry.grade] || 'tag-plain'">
                  {{ entry.grade_label || GRADE_LABELS[entry.grade] || entry.grade }}
                </span>
              </td>
            </tr>
          </tbody>
        </table>
        <div class="notice warn" style="margin-top: 12px">
          上表数字出自不同名单、不同发布主体、不同认定层级，<strong>互不相加、互不比较</strong>。
          规模字段一律存「数值 + 单位 + 口径 + 年份 + 来源 + 核验日期」，禁止裸数字与跨口径比较。
        </div>
      </section>

      <section class="section">
        <div class="card panel">
          <h3>企业-集群归属规则</h3>
          <ol class="boundary-list">
            <li>主判据：注册地所在镇/街道 + 主营产品，两者指向同一集群时直接归属。</li>
            <li>次判据：两者冲突时以<strong>主营产品</strong>为准，注册地仅作地理归属。</li>
            <li>多归属：企业跨集群经营时允许一企多集群，但必须指定一个主集群。</li>
            <li>兜底：无法判定时归入注册地所在县的主导集群，并在页面标注「归属待确认」。</li>
          </ol>
          <p class="small muted">
            规则公开 = 异议有据可依 = 更正流程可执行。企业可通过更正通道提交可核依据。
          </p>
        </div>
      </section>

      <section class="section">
        <div class="card panel">
          <h3>退出边界</h3>
          <ul class="boundary-list">
            <li>不下单、不收款、不展示报价、不撮合、不抽成、不保证回复。</li>
            <li>不收“入驻门票”、不卖排名、不接受竞价、不做付费认证与等级背书。</li>
            <li>只展示公开地址与官网公示的企业联系方式，不含任何个人联系方式。</li>
            <li>站内问答（规划中）只检索站内实体库，不访问外部网络，并标注「内容由 AI 生成」，上线前完成算法备案与公示。</li>
          </ul>
        </div>
      </section>

      <section class="section">
        <div class="card panel">
          <h3>账号与隐私</h3>
          <ul class="boundary-list">
            <li v-for="(rule, index) in payload.account || []" :key="index">{{ rule }}</li>
            <li>本站不采集任何<strong>个人联系方式</strong>——企业页只展示官网公示的总机/400 与公开邮箱，手机号一律不采集、不展示。</li>
          </ul>
        </div>
      </section>

      <section class="section">
        <div class="card panel">
          <h3>时点与免责</h3>
          <p class="small muted">
            企业名单以官方公示原文为准，核验时点见各家企业页面的「核验日期」。
            镇上街道级块状经济（C 级）无统一官方名单，属站方整理，未逐条核验，仅作检索线索使用。
            本站为公开信息整理工具，不构成对任何产业集群、企业或交易的建议。
          </p>
        </div>
      </section>
    </template>
  </div>
</template>

<style scoped>
.panel {
  padding: 16px;
}

h3 {
  font-size: 16px;
  margin: 0 0 10px;
}

table.card {
  overflow: hidden;
}

/* --------------------------------------------------------------------------
   来源地图：按核实程度分组的行。
   名称与档位同一行读完，用途说明占满整行宽度 —— 中文名单名很长，
   塞进窄列会折成五行，而说明本身是上百字，两侧都读不下去。
   -------------------------------------------------------------------------- */
.source-group + .source-group {
  margin-top: 18px;
}

.source-group-head {
  display: flex;
  align-items: center;
  gap: 8px;
  padding-bottom: 6px;
  border-bottom: 1px solid var(--rule-strong);
}

.source-list {
  display: flex;
  flex-direction: column;
}

.source-row {
  padding: 12px 2px;
  border-bottom: 1px solid var(--rule);
}

.source-row:last-child {
  border-bottom: none;
}

.source-title {
  display: flex;
  align-items: baseline;
  flex-wrap: wrap;
  gap: 8px;
  font-size: 15px;
  line-height: 1.5;
}

/* 用途说明：比正文小一档、颜色淡一档，但**占满行宽**，
   这样 300 字的说明读起来是 2-3 行，而不是一个挤在角落的方块。 */
.source-use {
  margin: 4px 0 0;
  font-size: 13px;
  line-height: 1.7;
  color: var(--ink-2);
  max-width: 88ch;
}
</style>