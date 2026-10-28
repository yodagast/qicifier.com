<script setup>
import { computed, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { login as doLogin, register as doRegister, session } from '../session'

const route = useRoute()
const router = useRouter()

const mode = ref(route.query.mode === 'register' ? 'register' : 'login')
const form = ref({ username: '', password: '', password2: '', display_name: '' })
const busy = ref(false)
const error = ref('')
const showPassword = ref(false)
const showPassword2 = ref(false)

const isRegister = computed(() => mode.value === 'register')
// 令牌过期 / 账号被清理时会走到这里：明确说一句，别让用户以为密码记错了
const expired = computed(() => session.expired)

/**
 * 登录后去哪。`redirect` 来自 URL，**只能接受站内路径** ——
 * 直接把它交给 router.replace，等于让外部构造的链接决定用户登录后跳到哪
 * （开放重定向）。`//evil.com` 在浏览器里是协议相对地址，也要挡住。
 */
const redirect = computed(() => {
  const target = typeof route.query.redirect === 'string' ? route.query.redirect : ''
  if (!target.startsWith('/') || target.startsWith('//') || target.startsWith('/login')) return '/'
  return target
})

const USERNAME_RE = /^[A-Za-z0-9_\u4e00-\u9fa5]{3,24}$/

const usernameError = computed(() => {
  const value = form.value.username.trim()
  if (!value) return ''
  return USERNAME_RE.test(value) ? '' : '3-24 位字母、数字、下划线或汉字'
})

const passwordMismatch = computed(
  () => isRegister.value && form.value.password2 !== '' && form.value.password !== form.value.password2,
)

// 按钮是否可点：把「还差什么」直接体现在按钮状态上，
// 而不是让用户点一下、再读一行红字、再改一次。
const canSubmit = computed(() => {
  if (busy.value) return false
  if (!form.value.username.trim() || !form.value.password) return false
  if (usernameError.value) return false
  if (isRegister.value) {
    if (form.value.password.length < 8) return false
    if (form.value.password !== form.value.password2) return false
  }
  return true
})

function switchMode(next) {
  if (mode.value === next) return
  mode.value = next
  error.value = ''
  // 切模式时清掉密码：注册页的密码留在登录页的输入框里没有道理，
  // 被旁人看到屏幕也不合适。
  form.value.password = ''
  form.value.password2 = ''
  showPassword.value = false
  showPassword2.value = false
}

async function submit() {
  error.value = ''
  if (usernameError.value) {
    error.value = usernameError.value
    return
  }
  if (isRegister.value) {
    if (form.value.password.length < 8) {
      error.value = '密码至少 8 位'
      return
    }
    if (form.value.password !== form.value.password2) {
      error.value = '两次输入的密码不一致'
      return
    }
  }
  busy.value = true
  try {
    if (isRegister.value) {
      await doRegister({
        username: form.value.username.trim(),
        password: form.value.password,
        display_name: form.value.display_name.trim(),
      })
    } else {
      await doLogin({ username: form.value.username.trim(), password: form.value.password })
    }
    // 跳转统一交给下面那个 watch —— 只保留一条导航路径，
    // 两处各 replace 一次会在控制台留下重复导航的报错。
  } catch (err) {
    error.value = String(err.message || err)
  } finally {
    busy.value = false
  }
}

// 已登录时不该停在登录页。判据用 session.ready 而不是直接看 user：
// restore() 是异步的，挂载那一刻还没拉回用户，早退会漏掉「刷新后仍登录」这种情况。
watch(
  () => [session.ready, session.user],
  () => {
    if (session.ready && session.user && route.name === 'login') {
      router.replace(redirect.value)
    }
  },
  { immediate: true },
)
</script>

<template>
  <div class="container">
    <section class="section auth">
      <div class="card panel auth-card">
        <header class="auth-head">
          <h1>{{ isRegister ? '创建账号' : '登录' }}</h1>
          <p class="small muted">
            {{ isRegister ? '收藏企业、记下自己的采购线索。' : '登录后查看你的收藏与备注。' }}
          </p>
        </header>

        <div class="auth-tabs" role="tablist" aria-label="登录或注册">
          <button
            type="button"
            role="tab"
            :aria-selected="!isRegister"
            :class="{ active: !isRegister }"
            @click="switchMode('login')"
          >
            登录
          </button>
          <button
            type="button"
            role="tab"
            :aria-selected="isRegister"
            :class="{ active: isRegister }"
            @click="switchMode('register')"
          >
            注册
          </button>
        </div>

        <p v-if="expired" class="notice warn" style="margin-bottom: 14px">
          <strong>登录状态已失效</strong>，请重新登录。
        </p>

        <p v-if="isRegister" class="auth-hint">无需邮箱或短信验证，填用户名和密码即可使用。</p>

        <form @submit.prevent="submit" novalidate>
          <label class="field">
            <span class="field-label">用户名</span>
            <input
              v-model="form.username"
              type="text"
              autocomplete="username"
              placeholder="3-24 位字母、数字、下划线或汉字"
              :aria-invalid="Boolean(usernameError)"
              required
            />
            <span v-if="usernameError" class="field-error">{{ usernameError }}</span>
          </label>

          <label class="field">
            <span class="field-label">密码</span>
            <span class="input-wrap">
              <input
                v-model="form.password"
                :type="showPassword ? 'text' : 'password'"
                :autocomplete="isRegister ? 'new-password' : 'current-password'"
                :placeholder="isRegister ? '至少 8 位，不要用纯数字' : '请输入密码'"
                required
              />
              <button
                type="button"
                class="reveal"
                :aria-label="showPassword ? '隐藏密码' : '显示密码'"
                @click="showPassword = !showPassword"
              >
                {{ showPassword ? '隐藏' : '显示' }}
              </button>
            </span>
          </label>

          <label v-if="isRegister" class="field">
            <span class="field-label">确认密码</span>
            <span class="input-wrap">
              <input
                v-model="form.password2"
                :type="showPassword2 ? 'text' : 'password'"
                autocomplete="new-password"
                placeholder="再输一次"
                required
              />
              <button
                type="button"
                class="reveal"
                :aria-label="showPassword2 ? '隐藏密码' : '显示密码'"
                @click="showPassword2 = !showPassword2"
              >
                {{ showPassword2 ? '隐藏' : '显示' }}
              </button>
            </span>
            <span v-if="passwordMismatch" class="field-error">两次输入的密码不一致</span>
          </label>

          <label v-if="isRegister" class="field">
            <span class="field-label">显示名称 <span class="tiny muted">（可选）</span></span>
            <input v-model="form.display_name" type="text" placeholder="例如：采购小王" autocomplete="nickname" />
          </label>

          <p v-if="error" class="notice warn" role="alert">{{ error }}</p>

          <button class="primary auth-submit" type="submit" :disabled="!canSubmit">
            {{ busy ? '提交中…' : isRegister ? '注册并登录' : '登录' }}
          </button>
        </form>

        <p class="auth-foot tiny muted">
          密码不会以明文保存；收藏与备注仅你本人可见。
          <RouterLink to="/service#privacy">查看隐私条款</RouterLink>
        </p>
      </div>
    </section>
  </div>
</template>

<style scoped>
.auth {
  max-width: 430px;
  margin: 0 auto;
  padding-top: 34px;
}

.auth-card {
  padding: 26px 26px 22px;
}

.auth-head {
  margin-bottom: 18px;
}

.auth-head h1 {
  margin: 0 0 6px;
  font-size: var(--fs-lg);
  letter-spacing: 0.01em;
}

.auth-head p {
  margin: 0;
}

/* 分段控件：一条细线包住两个等宽按钮，选中态用白底 + 品牌色字。
   比两个各自带边框的按钮更像「同一组里的两种状态」。 */
.auth-tabs {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 4px;
  padding: 4px;
  margin-bottom: 18px;
  background: var(--paper);
  border: 1px solid var(--rule);
  border-radius: 10px;
}

.auth-tabs button {
  padding: 8px 0;
  font-size: var(--fs-sm);
  border: 1px solid transparent;
  border-radius: 7px;
  background: transparent;
  color: var(--ink-2);
  transition: background 0.15s, color 0.15s;
}

.auth-tabs button:hover:not(.active) {
  color: var(--ink);
}

.auth-tabs button.active {
  background: var(--panel);
  border-color: var(--rule-strong);
  color: var(--seal);
  font-weight: 600;
}

.auth-hint {
  margin: 0 0 16px;
  padding: 9px 12px;
  font-size: var(--fs-xs);
  line-height: 1.65;
  color: var(--ink-2);
  background: var(--jade-soft);
  border-left: 2px solid var(--jade);
  border-radius: 0 var(--r-xs) var(--r-xs) 0;
}

form {
  display: flex;
  flex-direction: column;
  gap: 14px;
}

.field {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.field-label {
  font-size: var(--fs-xs);
  color: var(--ink-2);
  letter-spacing: 0.02em;
}

.field input {
  width: 100%;
  padding: 9px 11px;
  font-size: var(--fs-base);
}

.field-error {
  font-size: var(--fs-xs);
  color: var(--seal);
}

/* 密码框右侧的「显示 / 隐藏」：像文本框内的一个按钮，
   不抢视觉，但一点就能核对输入。 */
.input-wrap {
  position: relative;
  display: block;
}

.input-wrap input {
  padding-right: 58px;
}

.reveal {
  position: absolute;
  top: 50%;
  right: 6px;
  transform: translateY(-50%);
  padding: 4px 8px;
  font-size: var(--fs-xs);
  color: var(--ink-3);
  background: transparent;
  border: none;
  border-radius: var(--r-xs);
}

.reveal:hover {
  color: var(--seal);
  background: transparent;
}

.auth-submit {
  width: 100%;
  padding: 10px;
  margin-top: 2px;
  font-size: var(--fs-base);
}

.auth-foot {
  margin: 18px 0 0;
  line-height: 1.7;
}

.auth-foot a {
  color: var(--jade);
}
</style>