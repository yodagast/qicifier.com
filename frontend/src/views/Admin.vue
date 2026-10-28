<script setup>
import { computed, onMounted, ref, nextTick, watch } from 'vue'
import { useRoute } from 'vue-router'
// 复用 api.js 的底层请求（它把「非 JSON 的失败响应」处理好了）。
// 本页原先自己写了一份 fetch + `JSON.parse(text)`，漏了那一层：后端 500 时
// Starlette 回的是纯文本 `Internal Server Error`，于是用户看到的报错是
// `Unexpected token 'I', "Internal S"... is not valid JSON` —— 既看不出发生了什么，
// 也看不出「该不该重试」。同一个坑不要再有第二份实现。
import { request } from '../api'

const TOKEN_KEY = 'zanzhu.adminToken'

const route = useRoute()

const token = ref('')
const unlocked = ref(false)
const gaps = ref([])
const total = ref(0)
const statusFilter = ref('empty')
const keyword = ref('')
const loading = ref(false)
const message = ref('')
const error = ref('')
const form = ref(blankForm())
// 纯布尔标志，只用于「保存中…」与按钮禁用态（与 import 那一块的 `importing` 一致）
const saving = ref(false)

// 已经人工补充过的条目：可以在这里看到并撤回。
// 人工补充优先级最高，填错会盖住站方核验层的结果，所以必须能撤销 ——
// 一个「优先级最高」的层没有撤销入口，是个迟早会被用到的缺口。
const manual = ref([])

// 名单批量导入（/api/admin/lists/import）：清单是会修订的，改一次就该导一次，
// 而不是再写一个解析脚本。干跑先把报告拿出来看，确认无误再真导。
const lists = ref([])
const importForm = ref({ list_name: '', source_key: '', grade: 'site_curated', source_file: '', used_for: '' })
const importFile = ref(null)
// 文件选择框的 DOM 引用：导入成功后必须把它的 value 清空（见 upload 里的注释）。
// 不能用 `importFile = null` 代替 —— input 自己还留着旧文件，
// 再点一次导入会用**旧文件**覆盖，而屏幕上显示的是新选的文件名。
const fileInput = ref(null)
const importReport = ref(null)
const importing = ref(false)
// 「覆盖哪一份已有名单」。留空 = 新增。
//
// 这里**不能**像以前那样在打开页面时把最后一份名单的 list_name / source_key
// 预填进表单：同一个 source_key 再导入 = 覆盖，于是「导一份新名单」会静默
// 变成「覆盖上一份名单」—— 上一份的 264 行直接消失，页面数字几乎不变，
// 而用户以为自己只是新增。覆盖必须是一次显式选择。
const overwriteKey = ref('')

// 重新灌库（POST /api/admin/lists/reseed）：导入名单 / 保存人工补充 / 改产业带档案
// 都只写 data/ 下的产物文件，接口刻意不碰数据库；不灌库页面永远不变。
// 此前这一步只能回终端敲 `seed --drop`，抄错一个字就是一次失败的灌库 ——
// 所以给同一个后端函数配个按钮。灌库会删重建整张 enterprise 表，所以要先确认。
const reseeding = ref(false)

// 导入 → 灌库这条链路的**就地**状态。
//
// 以前只用页面顶部的 message / error，而导入区块在本页最底部：灌库要跑
// 约一分钟，成功或失败都发生在视野之外 —— 用户看到的就是「导完了，
// 首页的数字没变」。现在结果写在按钮旁边，并且导入成功后自动灌库。
const flow = ref({ tone: 'idle', text: '' })

function setFlow(tone, text) {
  flow.value = { tone, text }
}
const GRADES = [
  { key: 'official', label: '官方认定' },
  { key: 'gov_open', label: '政府公开信息' },
  { key: 'media', label: '媒体 · 协会来源' },
  { key: 'site_curated', label: '站方整理（第三方调研清单）' },
  { key: 'self_reported', label: '企业自报 · 本站未核验' },
]

function blankForm() {
  return { name: '', website: '', public_phone: '', public_email: '', address: '', source_url: '', note: '' }
}

const filtered = computed(() => {
  const key = keyword.value.trim()
  if (!key) return gaps.value
  return gaps.value.filter((row) => row.name.includes(key))
})

/** 管理接口共用一个令牌头；其余（含失败响应的解读）交给 `request`。 */
function call(path, options = {}) {
  return request(path, {
    ...options,
    headers: { 'X-Admin-Token': token.value, ...(options.headers || {}) },
  })
}

async function load() {
  loading.value = true
  error.value = ''
  try {
    const data = await call(`/admin/contacts/gaps?contact_status=${statusFilter.value}&limit=1000`)
    gaps.value = data.items
    total.value = data.total
    unlocked.value = true
    localStorage.setItem(TOKEN_KEY, token.value)
    loadLists()
    loadResearch()
  } catch (err) {
    unlocked.value = false
    error.value = String(err.message || err)
  } finally {
    loading.value = false
  }
}

function edit(row) {
  form.value = {
    name: row.name,
    website: row.website || '',
    public_phone: row.public_phone || '',
    public_email: row.public_email || '',
    address: '',
    source_url: '',
    note: '',
  }
  message.value = ''
  error.value = ''
  window.scrollTo({ top: 0, behavior: 'smooth' })
}

async function save() {
  saving.value = true
  message.value = ''
  error.value = ''
  try {
    const result = await call('/admin/contacts', {
      method: 'POST',
      body: JSON.stringify(form.value),
    })
    message.value = `${result.updated ? '已更新' : '已新增'}「${form.value.name}」的公开联系方式。点下方「按 data/ 清单重新灌库」后生效。`
    gaps.value = gaps.value.filter((row) => row.name !== form.value.name)
    total.value = Math.max(0, total.value - 1)
    form.value = blankForm()
    loadManual()
  } catch (err) {
    error.value = String(err.message || err)
  } finally {
    saving.value = false
  }
}

async function upload(dryRun) {
  if (!importFile.value || !importForm.value.list_name || !importForm.value.source_key) {
    error.value = '请选择 CSV 文件，并填写名单全名与 source_key'
    return
  }
  // 兜底：source_key 撞上已有名单却没在「覆盖」里显式选过 —— 拦住。
  // 后端把同名当覆盖是**正确**语义（修订版就该覆盖），错的是「没打算覆盖却覆盖了」。
  const clash = lists.value.find((row) => row.source_key === importForm.value.source_key)
  if (clash && overwriteKey.value !== clash.source_key) {
    error.value =
      `source_key「${importForm.value.source_key}」已被「${clash.list_name}」占用。` +
      '要修订它，请在上方「覆盖已有名单」里选中它；要新增，请换一个 source_key。'
    return
  }
  importing.value = true
  message.value = ''
  error.value = ''
  importReport.value = null
  try {
    const body = new FormData()
    body.append('file', importFile.value)
    for (const [key, value] of Object.entries(importForm.value)) body.append(key, value)
    body.append('dry_run', dryRun ? 'true' : 'false')
    // FormData 的 Content-Type 必须由浏览器自己带（含 boundary）；`request`
    // 只在 body 是字符串时才写 application/json，所以这里可以直接传 FormData。
    const payload = await call('/admin/lists/import', { method: 'POST', body })
    importReport.value = payload
    if (dryRun) {
      message.value = '干跑完成，未写入任何文件。'
      return
    }
    // 消息里必须带上「相对现有库新增多少家」—— 导完一份**没有新企业**的名单
    // （全部已在其它名单里）时，首页数字本来就不会动，而屏幕上只写「已导入 594 行」
    // 会让那件事看起来像 bug。这句话才是「为什么数字没变」的答案。
    const added = payload.database?.new_to_db ?? null
    const alsoIn = payload.database?.also_in_other_lists ?? null
    const delta =
      added === null
        ? ''
        : added === 0
          ? `新增 0 家（这 ${alsoIn ?? 0} 家已在其它名单里，收录总数不会变）。`
          : `新增 ${added} 家企业。`
    message.value = payload.replaced
      ? `已覆盖「${payload.list_name}」（${payload.previous_rows ?? '?'} 行 → ${payload.rows} 行）：${delta}`
      : `已导入「${payload.list_name}」（${payload.rows} 行）：${delta}`
    loadLists()
    // 导入完成后把表单切到「覆盖这份名单」并把文件清空：
    //   1. 不切的话，source_key 已入库，再点「导入为新名单」会被下面的撞车检查拦住
    //      ——按钮写着能点、点了却报错，是最没必要的一种挫败；
    //   2. 切过去之后，改一版 CSV 再导 = 修订这份名单，这才是接下来最可能做的事；
    //   3. 文件必须清空：input 里的旧文件还在，重复点导入会**用旧文件覆盖**，
    //      而屏幕上显示的是新选的文件名（浏览器不会重置 file input 的 value）。
    overwriteKey.value = importForm.value.source_key
    importFile.value = null
    if (fileInput.value) fileInput.value.value = ''
    // 导入只写 data/ 下的产物文件，数据库不会动 —— 不灌库，首页「已收录企业」
    // 永远不会变。以前这一步留给用户自己再点一次按钮，结果就是「导完了没反应」。
    // 现在导入成功后直接接着灌库，用户只需要看结果。
    await reseed({ confirmFirst: false })
  } catch (err) {
    error.value = String(err.message || err)
    setFlow('error', `导入失败：${err.message || err}`)
  } finally {
    importing.value = false
  }
}

async function reseed({ confirmFirst = true } = {}) {
  if (confirmFirst) {
    const ok = window.confirm(
      '重新灌库会按 data/ 下的清单重建数据层（地区 / 集群 / 企业），' +
        '期间约 1 分钟内所有查询可能落空。\n账号与收藏不受影响。确定继续？',
    )
    if (!ok) return false
  }
  reseeding.value = true
  const startedAt = Date.now()
  try {
    setFlow('busy', '正在按 data/ 清单重建数据层…（约 1 分钟，账号与收藏保留）')
    const result = await reseedWithRetry()
    const counts = result.payload.counts || {}
    const seconds = Math.round((Date.now() - startedAt) / 1000)
    setFlow(
      'ok',
      `已生效（用时 ${seconds} 秒${result.retried ? '，第 2 次成功' : ''}）：` +
        `地区 ${counts.regions} · 集群 ${counts.clusters} · 企业 ${counts.enterprises}。` +
        '首页「已收录企业」等数字在刷新后即为最新。',
    )
    // 灌库会重建数据层，待补充名单与已有联系方式都可能变，顺手刷新
    load()
    return true
  } catch (err) {
    // 失败必须写在按钮旁边：产物文件已写入 data/，数据没丢，
    // 重新点一次灌库即可 —— 这个事实要让人看得见，否则只能重导一遍。
    setFlow(
      'error',
      `灌库失败：${err.message || err}\n产物已写入 data/，不用重导；稍后点「按 data/ 清单重新灌库」重试即可。`,
    )
    return false
  } finally {
    reseeding.value = false
  }
}

/**
 * 这次失败值不值得再试一次？
 *
 * 判据要**窄**：灌库本身要跑一分钟以上，对着一个真错误重试只是把等待翻倍。
 *   0        —— 网络断了（后端正在重启时最常见）
 *   502/503/504 —— 网关/代理层，后端没在听
 *   500 且**没有 JSON body** —— Starlette 的纯文本 `Internal Server Error`：
 *              说明异常没进到我们自己的 try/except（进程正在重启 / 崩了），
 *              而不是「灌库跑完但失败」（那种是 HTTPException，带 detail，**不重试**）。
 */
function isTransient(err) {
  if (!err || typeof err.status !== 'number') return false
  if (err.status === 0) return true
  if ([502, 503, 504].includes(err.status)) return true
  return err.status === 500 && !err.payload
}

/** 灌库是幂等的（每次按 data/ 重建），所以暂态失败重试一次是安全的。 */
async function reseedWithRetry() {
  try {
    return { payload: await call('/admin/lists/reseed', { method: 'POST' }), retried: false }
  } catch (err) {
    if (!isTransient(err)) throw err
    setFlow('busy', `服务端刚刚不可用（${err.status || '网络'}），3 秒后自动重试一次…`)
    await new Promise((resolve) => setTimeout(resolve, 3000))
    return { payload: await call('/admin/lists/reseed', { method: 'POST' }), retried: true }
  }
}

async function loadManual() {
  try {
    const data = await call('/admin/contacts')
    manual.value = data.companies || []
  } catch (err) {
    error.value = String(err.message || err)
  }
}

async function removeManual(row) {
  if (!window.confirm(`撤回「${row.name}」的人工补充？（会回退到站方核验的结果）`)) return
  saving.value = true
  message.value = ''
  error.value = ''
  try {
    // 企业名走查询参数：真实数据里有含斜杠的名字（Herman Miller / Steelcase），
    // 放进路径会被 %2F 解码后切分，落到 404 上。
    await call(`/admin/contacts?name=${encodeURIComponent(row.name)}`, { method: 'DELETE' })
    manual.value = manual.value.filter((r) => r.name !== row.name)
    message.value = `已撤回「${row.name}」的人工补充。点下方「按 data/ 清单重新灌库」后生效。`
  } catch (err) {
    error.value = String(err.message || err)
  } finally {
    saving.value = false
  }
}

async function loadLists() {
  try {
    const data = await call('/admin/lists')
    lists.value = data.lists
    // 刻意**不**预填表单：见 overwriteKey 上的注释。
    // 「修订某份名单」由用户在下拉里显式选，选择时才把那份名单的字段带过来。
  } catch (err) {
    error.value = String(err.message || err)
  }
}

// 选中「覆盖哪一份」→ 带出它的元数据（名单名/核实程度/来源文件/说明），
// 这些是发布口径，重导时不该让用户重打一遍，也最容易打错。
function pickOverwrite(key) {
  overwriteKey.value = key
  importReport.value = null
  if (!key) return
  const entry = lists.value.find((row) => row.source_key === key)
  if (!entry) return
  importForm.value.list_name = entry.list_name
  importForm.value.source_key = entry.source_key
  importForm.value.grade = entry.grade
  importForm.value.source_file = entry.source_file || ''
  importForm.value.used_for = entry.used_for || ''
}

const overwriteTarget = computed(() =>
  overwriteKey.value ? lists.value.find((row) => row.source_key === overwriteKey.value) : null,
)

// ── 行业研究（/api/admin/research）────────────────────────────────────
//
// 与名单导入**不同**：研究文档导入即生效，不需要灌库 —— 它不进数据库，
// 由 FastAPI 直接读出 data/research/<slug>.html 直出给浏览器与爬虫。
//
// 也正因为直出，导入的是**整份 HTML 文档**（自带 <style>），不是正文片段；
// 后端会用 CSP 封掉文档里的脚本，导入时若检测到 <script> 会明确告警。
const researchDocs = ref([])
const researchForm = ref({ slug: '', title: '', summary: '', published_at: '' })
const researchFile = ref(null)
// 与 fileInput 同理：上传成功后必须清空 input.value，
// 否则重复点上传会用**旧文件**覆盖，而屏幕上显示的是新选的文件名。
const researchInput = ref(null)
const researchBusy = ref(false)
const researchFlow = ref({ tone: 'idle', text: '' })

async function loadResearch() {
  try {
    const data = await call('/admin/research')
    researchDocs.value = data.items || []
  } catch (err) {
    error.value = String(err.message || err)
  }
}

async function importResearch() {
  if (!researchFile.value) {
    researchFlow.value = { tone: 'error', text: '请先选择一份 HTML 文档' }
    return
  }
  researchBusy.value = true
  researchFlow.value = { tone: 'busy', text: '正在导入…' }
  try {
    const body = new FormData()
    body.append('file', researchFile.value)
    for (const [key, value] of Object.entries(researchForm.value)) body.append(key, value)
    const payload = await call('/admin/research/import', { method: 'POST', body })
    const warnings = payload.warnings || []
    researchFlow.value = {
      tone: 'ok',
      text:
        `已导入「${payload.article.title}」，地址 ${payload.url}（导入即生效，不需要灌库）` +
        (warnings.length ? `。注意：${warnings.join('；')}` : ''),
    }
    researchFile.value = null
    if (researchInput.value) researchInput.value.value = ''
    researchForm.value = { slug: '', title: '', summary: '', published_at: '' }
    loadResearch()
  } catch (err) {
    researchFlow.value = { tone: 'error', text: `导入失败：${err.message || err}` }
  } finally {
    researchBusy.value = false
  }
}

async function removeResearch(doc) {
  if (!window.confirm(`下线「${doc.title}」？文档文件会一起删除，链接立即 404。`)) return
  researchBusy.value = true
  try {
    await call(`/admin/research/${encodeURIComponent(doc.slug)}`, { method: 'DELETE' })
    researchFlow.value = { tone: 'ok', text: `已下线「${doc.title}」` }
    loadResearch()
  } catch (err) {
    researchFlow.value = { tone: 'error', text: `下线失败：${err.message || err}` }
  } finally {
    researchBusy.value = false
  }
}

// 页脚的「数据导入」用 hash 落到 #import，但那个锚点在**解锁之后**才渲染出来
// （未解锁时只有一个令牌输入框），于是 RouterLink 的滚动找不到元素、
// 控制台报 "Couldn't find element using selector \"#import\""，用户看到的则是
// 「点了链接，页面却停在顶部」。所以这里在内容真正出现的那一次再滚一遍。
//
// 只认显式带 #import 的进入：否则从页脚点「返回本页」也会被莫名滚到中段。
async function scrollToImportIfAsked() {
  if (route.hash !== '#import') return
  await nextTick()
  document.querySelector('#import')?.scrollIntoView({ behavior: 'smooth', block: 'start' })
}

watch(unlocked, (value) => {
  if (value) scrollToImportIfAsked()
})

onMounted(() => {
  token.value = localStorage.getItem(TOKEN_KEY) || ''
  if (token.value) {
    load()
    loadLists()
    loadManual()
    loadResearch()
    // 令牌已在本地：模板会立刻展开，无需等 watch
    scrollToImportIfAsked()
  }
})
</script>

<template>
  <div class="container">
    <div class="page-head">
      <h1>人工补充公开联系方式</h1>
      <p class="lead">
        站方核验不出官网的企业在这里手工补全。写入
        <code>data/curated/manual_sites.json</code>，优先级高于站方核验结果，页面上会标为「人工补充 · 站方核对」。
      </p>
    </div>

    <section class="section">
      <div v-if="!unlocked" class="card panel">
        <label class="small muted">管理令牌（后端需设置环境变量 <code>ADMIN_TOKEN</code> 才会启用本页）</label>
        <div class="stack" style="margin-top: 8px">
          <input v-model="token" type="password" placeholder="X-Admin-Token" style="width: 280px" />
          <button class="primary" @click="load">解锁</button>
        </div>
        <p v-if="error" class="notice warn" style="margin-top: 12px">
          无法访问：{{ error }}
          <br />
          启用方式：<code>ADMIN_TOKEN=your-secret uvicorn app.main:app</code>
        </p>
      </div>

      <template v-else>
        <div class="notice" style="margin-bottom: 14px">
          <strong>填写边界</strong>：只填公开信息（官网、官网公示的总机/400、公开邮箱、公开地址）。
          不得填写个人手机号、个人邮箱或任何非公开联系方式——后端会直接拒绝手机号。
        </div>

        <p v-if="message" class="notice" style="margin-bottom: 14px">{{ message }}</p>
        <p v-if="error" class="notice warn" style="margin-bottom: 14px">{{ error }}</p>

        <div class="card panel">
          <h3>{{ form.name ? `补充：${form.name}` : '补充表单（点击下方列表中的企业载入）' }}</h3>
          <div class="form-grid">
            <label>企业名称<input v-model="form.name" type="text" placeholder="必须与官方名单完全一致" /></label>
            <label>官网<input v-model="form.website" type="text" placeholder="https://www.example.com/" /></label>
            <label>公开电话<input v-model="form.public_phone" type="text" placeholder="0571-88888888 / 400-888-8888" /></label>
            <label>公开邮箱<input v-model="form.public_email" type="text" placeholder="info@example.com" /></label>
            <label>公开地址<input v-model="form.address" type="text" placeholder="省 市 区 街道 门牌" /></label>
            <label>来源链接<input v-model="form.source_url" type="text" placeholder="证明上述信息的公开页面" /></label>
            <label class="wide">备注<input v-model="form.note" type="text" placeholder="例如：电话取自官网联系我们页面" /></label>
          </div>
          <div class="stack" style="margin-top: 12px">
            <!-- 判据用纯布尔 ref：把「企业名」这类字符串 ref 直接当布尔用在 props 绑定里，
                 一旦它落到 ref 对象而非解包值上就是恒真 —— 按钮永远灰着，
                 而文案却正常切换（文本插值会解包），看起来像「点了没反应」。 -->
            <button class="primary" :disabled="!form.name || saving" @click="save">
              {{ saving ? '保存中…' : '保存到 manual_sites.json' }}
            </button>
            <button @click="form = blankForm()">清空</button>
          </div>
        </div>

        <div class="row" style="margin-top: 22px">
          <h3>已人工补充（{{ manual.length }}）</h3>
          <span class="tiny muted">这一层优先级最高，会盖住站方核验层的结果；填错可以在这里撤回</span>
        </div>
        <div v-if="!manual.length" class="empty">还没有人工补充的记录</div>
        <table v-else class="card list">
          <thead>
            <tr>
              <th>企业名称</th>
              <th>官网</th>
              <th>电话</th>
              <th>地址</th>
              <th>备注</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="row in manual" :key="row.name">
              <td>{{ row.name }}</td>
              <td class="small muted">{{ row.website || '—' }}</td>
              <td class="small muted mono">{{ row.public_phone || '—' }}</td>
              <td class="small muted">{{ row.address || '—' }}</td>
              <td class="small muted">{{ row.note || '—' }}</td>
              <td>
                <button class="tiny-btn" :disabled="saving" @click="removeManual(row)">撤回</button>
              </td>
            </tr>
          </tbody>
        </table>

        <div class="row" style="margin-top: 22px">
          <h3>待补充企业（{{ total }}）</h3>
          <div class="stack">
            <select v-model="statusFilter" @change="load">
              <option value="empty">完全没有联系方式</option>
              <option value="partial">联系方式不完整</option>
              <option value="full">已有完整联系方式</option>
            </select>
            <input v-model="keyword" type="search" placeholder="筛选企业名" />
          </div>
        </div>

        <div v-if="loading" class="loading">加载中…</div>
        <div v-else-if="!filtered.length" class="empty">没有匹配的企业</div>
        <table v-else class="card list">
          <thead>
            <tr>
              <th>企业名称</th>
              <th>地区</th>
              <th>现有官网</th>
              <th>现有电话</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="row in filtered.slice(0, 200)" :key="row.name">
              <td>{{ row.name }}</td>
              <td class="small muted mono">{{ row.city }}{{ row.district }}</td>
              <td class="small muted">{{ row.website || '—' }}</td>
              <td class="small muted mono">{{ row.public_phone || '—' }}</td>
              <td><button class="tiny-btn" @click="edit(row)">补充</button></td>
            </tr>
          </tbody>
        </table>
        <p v-if="filtered.length > 200" class="tiny muted" style="margin-top: 8px">
          仅显示前 200 条，请用筛选缩小范围。
        </p>

        <!-- id="import" 是页脚的「数据导入」入口落点：导入功能一直在这里，
             但此前没有任何能直达它的地址，页脚链接只能把人丢到页面顶部，
             再自己往下翻。锚点与页脚 RouterLink 的 hash 是一对，改名要同步改。 -->
        <div id="import" class="row" style="margin-top: 26px">
          <h3>批量导入名单 CSV</h3>
        </div>
        <p class="hint">
          一份外部分档清单 → 证据层 + 联系方式层 + 候选官网层三份产物，并登记到
          <code>data/curated/imported_lists.json</code>，来源声明与联系方式分层会自动带上它，
          不必再改代码。<strong>同一个 source_key 再导入 = 覆盖该名单</strong>（修订版把增删一并生效）。
          清单给出的官网只作候选，必须通过本站验收门才会以「站方核验 · 已比对官网」入库。
        </p>

        <div class="card panel">
          <!-- 覆盖必须显式选：留空 = 新增名单。预填 source_key 会让「导新名单」
               静默变成「覆盖上一份」，所以这里什么都不预填。 -->
          <div class="form-grid" style="margin-bottom: 10px">
            <label class="wide">
              导入方式
              <select :value="overwriteKey" @change="pickOverwrite($event.target.value)">
                <option value="">新增一份名单（不改动已有名单）</option>
                <option v-for="entry in lists" :key="entry.source_key" :value="entry.source_key">
                  覆盖已有名单：{{ entry.list_name }}（{{ entry.source_key }}，当前 {{ entry.rows }} 行）
                </option>
              </select>
            </label>
          </div>
          <div v-if="overwriteTarget" class="notice warn" style="margin-bottom: 12px">
            <strong>这是一次覆盖导入</strong>：{{ overwriteTarget.list_name }}
            将以本次 CSV 的全部内容<b>取代</b>现有 {{ overwriteTarget.rows }} 行 ——
            清单里删掉的企业会跟着消失。要新增名单，请把「导入方式」改回「新增一份名单」，
            并换一个 source_key。
          </div>
          <div class="form-grid">
            <label>CSV 文件<input ref="fileInput" type="file" accept=".csv,text/csv" @change="importFile = $event.target.files[0]" /></label>
            <label>名单全名<input v-model="importForm.list_name" type="text" placeholder="例如：某某产业群企业清单（第三方调研）" /></label>
            <label>source_key<input v-model="importForm.source_key" type="text" placeholder="小写字母/数字/下划线，如 wuyi_outdoor" /></label>
            <label>
              核实程度
              <select v-model="importForm.grade">
                <option v-for="g in GRADES" :key="g.key" :value="g.key">{{ g.label }}</option>
              </select>
            </label>
            <label>原始文件（备查）<input v-model="importForm.source_file" type="text" placeholder="docs/xxx.csv" /></label>
            <label class="wide">来源声明里的说明<input v-model="importForm.used_for" type="text" placeholder="这份名单在库里做什么（留空则用自动生成的计数说明）" /></label>
          </div>
          <div class="stack" style="margin-top: 12px">
            <button :disabled="importing || !importFile" @click="upload(true)">干跑看报告</button>
            <button class="primary" :disabled="importing || !importFile" @click="upload(false)">
              {{ importing ? '处理中…' : overwriteTarget ? '导入并覆盖该名单' : '导入为新名单' }}
            </button>
          </div>

          <!-- 就地状态：成功/失败都写在按钮旁边。
               导入区块在本页最底部，灌库要跑约一分钟 —— 结果只写页面顶部
               等于没写，用户看到的仍然是「点了没反应」。 -->
          <p
            v-if="flow.text"
            class="flow"
            :class="`flow-${flow.tone}`"
            :role="flow.tone === 'error' ? 'alert' : 'status'"
          >
            {{ flow.text }}
          </p>

          <!-- 灌库：导入、人工补充、产业带档案都只写 data/ 下的产物文件，
               不灌库页面永远不变。按钮与终端命令走的是同一个后端函数。 -->
          <div class="row" style="margin-top: 14px">
            <h4 style="margin: 0; font-size: 14px">灌库（让产物文件生效）</h4>
            <button :disabled="reseeding || importing" @click="reseed()">
              {{ reseeding ? '灌库中…' : '按 data/ 清单重新灌库' }}
            </button>
          </div>
          <p class="hint" style="margin-top: 8px">
            导入名单 / 保存人工补充 / 修改产业带档案都只写 <code>data/</code> 下的产物文件，
            接口刻意不碰数据库。<strong>导入成功后会自动灌库</strong>，不需要再点一次；
            手动补充联系方式、修改产业带档案之后才需要点上面的按钮。
            这里与终端命令 <code>python -m app.cli seed --drop</code> 是同一条代码路径，
            只重建数据层（地区 / 集群 / 企业），<strong>账号与收藏保留</strong>。
            全程约 1 分钟，期间查询可能短暂失败，属正常。
          </p>

          <div v-if="importReport" class="notice" style="margin-top: 14px">
            <div><strong>{{ importReport.list_name }}</strong> · {{ importReport.rows }} 行 / {{ importReport.companies }} 家</div>
            <div class="tiny muted" style="margin-top: 6px">
              官网 {{ importReport.with_website }} · 电话 {{ importReport.with_phone }} · 地址 {{ importReport.with_address }}
              · 省外 {{ importReport.out_of_province }} · 带括注的登记名 {{ importReport.annotated_names }}
              · {{ importReport.replaced ? `覆盖 ${importReport.previous_rows} 行` : '新建名单' }}
            </div>
            <div class="tiny muted">档位：{{ Object.entries(importReport.tiers).map(([k, v]) => `${k} ${v}`).join(' / ') }}</div>
            <div v-if="importReport.batches && Object.keys(importReport.batches).length" class="tiny muted">
              批次：{{ Object.entries(importReport.batches).map(([k, v]) => `${k} ${v}`).join(' / ') }}
            </div>
            <div class="tiny muted" style="margin-top: 6px">
              相对现有库：新企业 {{ importReport.database.new_to_db }} 家
              <template v-if="importReport.database.also_in_other_lists">
                （另有 {{ importReport.database.also_in_other_lists }} 家已在别的名单里
                <template v-if="importReport.database.new_to_db === 0">
                  —— 这一份是**叠加**在已有收录之上的，所以首页的「已收录企业」不会变
                </template>
                ）
              </template>
              <template v-if="importReport.database.dropped_from_db.length">
                · 会少掉 {{ importReport.database.dropped_from_db.length }} 家（不在这一版里、也不属于其它名单）
              </template>
              · 集群归属 {{ importReport.cluster_mapping.resolved_rows }}
              / {{ importReport.cluster_mapping.total_rows }} 行
              <template v-if="Object.keys(importReport.cluster_mapping.bases).length">
                （按
                <template v-for="(n, basis, i) in importReport.cluster_mapping.bases" :key="basis">
                  <template v-if="i > 0">、</template>{{ basis }}列 {{ n }} 行
                </template>
                落位）
              </template>
            </div>
            <ul v-if="importReport.warnings.length" class="tiny" style="margin: 6px 0 0 16px">
              <li v-for="(w, i) in importReport.warnings" :key="i">{{ w }}</li>
            </ul>
            <!-- 落位明细：清单的「产业带 / 赛道」可能分散到多个县的产业群，
                 只报总数看不出它到底挂到了哪里。 -->
            <details v-if="importReport.cluster_mapping.requests.length" class="tiny" style="margin-top: 6px">
              <summary>集群落位明细（{{ importReport.cluster_mapping.requests.length }} 个分组）</summary>
              <ul style="margin: 4px 0 0 14px">
                <li v-for="g in importReport.cluster_mapping.requests" :key="g.label">
                  {{ g.label }} —— {{ g.rows }} 行
                  <template v-if="g.slug_summary">→ {{ g.slug_summary }}</template>
                  <span v-if="g.unresolved_rows" class="warn">（{{ g.unresolved_rows }} 行未落位）</span>
                </li>
              </ul>
            </details>
            <div v-if="importReport.cluster_mapping.unresolved.length" class="tiny warn" style="margin-top: 6px">
              下面这些行挂不上集群，不会出现在任何集群页（企业仍会入库）：
              <ul style="margin: 4px 0 0 14px">
                <li v-for="u in importReport.cluster_mapping.unresolved" :key="u.label">
                  {{ u.label }} —— {{ u.unresolved_rows }} 行
                  <template v-if="u.slug_summary">（已落位：{{ u.slug_summary }}）</template>
                </li>
              </ul>
            </div>
            <div class="tiny muted" style="margin-top: 6px">{{ importReport.next_step }}</div>
          </div>

          <table v-if="lists.length" class="card list" style="margin-top: 14px">
            <thead>
              <tr>
                <th>已导入名单</th>
                <th>source_key</th>
                <th>核实程度</th>
                <th>行数 / 企业</th>
                <th>来源文件</th>
                <th>导入时间</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="entry in lists" :key="entry.source_key">
                <td>{{ entry.list_name }}</td>
                <td class="small muted mono">{{ entry.source_key }}</td>
                <td class="small muted">{{ entry.grade }}</td>
                <td class="small muted mono">{{ entry.rows }} / {{ entry.companies }}</td>
                <td class="small muted">{{ entry.source_file || '—' }}</td>
                <td class="small muted mono">{{ entry.imported_at }}</td>
              </tr>
            </tbody>
          </table>
        </div>

        <!-- 行业研究：导入 HTML 文档，正文由 /research/<slug> 页面**嵌进站点外壳**显示。
             与上面「批量导入名单」是两条完全不同的链路（那条要灌库，这条不用），
             所以单独一块、单独一段说明 —— 混在一起会让人以为导入文档也要等一分钟。 -->
        <div id="research" class="row" style="margin-top: 26px">
          <h3>行业研究文档</h3>
        </div>
        <p class="hint">
          上传整份 HTML 文档（自带样式，例如 <code>docs/</code> 下的方案与调研报告），
          写入 <code>data/research/&lt;slug&gt;.html</code> 并登记到 <code>data/research/index.json</code>。
          <strong>导入即生效，不需要灌库</strong>；顶部导航与页脚的「行业研究」就是它的入口。
          正文会嵌在站点页面里显示（页头、页脚、导航都在），文档自带的样式只作用于正文区域。
          <strong>同一个 slug 再导入 = 覆盖该篇</strong>。
          <strong>发布后要跑一次 <code>npm run build:seo</code></strong>，新文章才会进入预渲染产物
          （否则收录引擎拿到的仍是旧快照；页面本身即时可看）。
          文档里的 <code>&lt;script&gt;</code> 出于安全会被剥掉、不会执行。
        </p>

        <div class="card panel">
          <div class="form-grid">
            <label>
              HTML 文档
              <input ref="researchInput" type="file" accept=".html,.htm,text/html" @change="researchFile = $event.target.files[0]" />
            </label>
            <label>
              slug（留空取文件名）
              <input v-model="researchForm.slug" type="text" placeholder="例如：浙江产业集群收录体系搭建方案" />
            </label>
            <label>
              标题（留空取文档 &lt;title&gt;）
              <input v-model="researchForm.title" type="text" placeholder="例如：浙江产业集群收录体系搭建方案" />
            </label>
            <label>
              发布日期（留空取今天）
              <input v-model="researchForm.published_at" type="text" placeholder="2026-10-28" />
            </label>
            <label class="wide">
              摘要（留空取首个段落前 160 字）
              <input v-model="researchForm.summary" type="text" placeholder="出现在列表页与搜索结果摘要里" />
            </label>
          </div>
          <div class="stack" style="margin-top: 12px">
            <button class="primary" :disabled="researchBusy || !researchFile" @click="importResearch">
              {{ researchBusy ? '处理中…' : '导入文档' }}
            </button>
          </div>
          <p
            v-if="researchFlow.text"
            class="flow"
            :class="`flow-${researchFlow.tone}`"
            :role="researchFlow.tone === 'error' ? 'alert' : 'status'"
          >
            {{ researchFlow.text }}
          </p>

          <table v-if="researchDocs.length" class="card list" style="margin-top: 14px">
            <thead>
              <tr>
                <th>标题</th>
                <th>slug</th>
                <th>发布日期</th>
                <th>更新</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="doc in researchDocs" :key="doc.slug">
                <td>
                  <a :href="`/research/${encodeURIComponent(doc.slug)}`" target="_blank" rel="noopener">{{ doc.title }}</a>
                </td>
                <td class="small muted mono">{{ doc.slug }}</td>
                <td class="small muted mono">{{ doc.published_at }}</td>
                <td class="small muted mono">{{ doc.updated_at }}</td>
                <td><button class="tiny-btn" :disabled="researchBusy" @click="removeResearch(doc)">下线</button></td>
              </tr>
            </tbody>
          </table>
        </div>
      </template>
    </section>
  </div>
</template>

<style scoped>
.panel {
  padding: 16px;
}

.panel h3 {
  margin: 0 0 10px;
  font-size: 15px;
}

.form-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 10px;
}

.form-grid label {
  display: flex;
  flex-direction: column;
  gap: 4px;
  font-size: 13px;
  color: var(--muted);
}

.form-grid label.wide {
  grid-column: 1 / -1;
}

.form-grid input {
  width: 100%;
}

@media (max-width: 700px) {
  .form-grid {
    grid-template-columns: 1fr;
  }
}

table.list {
  overflow: hidden;
  margin-top: 12px;
}

.tiny-btn {
  padding: 3px 10px;
  font-size: 12px;
}

/* 导入 / 灌库的就地状态条。用左侧竖线区分状态，不新增图标，
   与站内 .notice / 来源说明块的写法一致。 */
.flow {
  margin: 12px 0 0;
  padding: 9px 12px;
  font-size: 12px;
  line-height: 1.7;
  /* 错误信息里带换行（产物已写入 / 可重试），要按换行断开 */
  white-space: pre-line;
  border: 1px solid var(--rule);
  border-left: 2px solid var(--rule-strong);
  border-radius: 0 var(--r-xs) var(--r-xs) 0;
  background: var(--paper);
  color: var(--ink-2);
}

.flow-busy {
  border-left-color: var(--jade);
  background: var(--jade-soft);
}

.flow-ok {
  border-left-color: var(--official);
  background: #f2f7f3;
}

.flow-error {
  border-left-color: var(--seal);
  background: var(--seal-soft);
  color: var(--seal);
}
</style>