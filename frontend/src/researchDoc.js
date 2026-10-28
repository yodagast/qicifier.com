// 把后台导入的「整份 HTML 文档」变成能安全嵌进本站页面的片段。
//
// 两件事，都不能省：
//   1. **清洗** —— 这份内容进的是同源页面，登录令牌就在 localStorage 里。
//      后端已经剥掉 <script>，这里再按 DOM 走一遍（on* 事件、javascript: 链接、
//      iframe/object/embed 等）。用 DOMParser 解析是真安全的一步：
//      它不会执行脚本，只有**重新序列化**出来的字符串才会交给 v-html。
//   2. **样式作用域** —— 文档自带 `<style>`，里面的 `:root{}` / `body{}` / `*{}`
//      原样注入会改掉全站排版（还会破坏页头页脚）。所以每条选择器都加上
//      `.research-doc` 前缀，让文档只影响它自己那一块。

const DROP_TAGS = new Set(['SCRIPT', 'IFRAME', 'OBJECT', 'EMBED', 'LINK', 'META', 'BASE', 'FORM'])
const URL_ATTRS = new Set(['href', 'src', 'xlink:href', 'formaction', 'action'])

/** 清洗正文片段，返回可交给 v-html 的字符串。 */
export function sanitizeBody(html) {
  const parsed = new DOMParser().parseFromString(`<body>${html || ''}</body>`, 'text/html')
  const body = parsed.body

  const doomed = []
  const walker = parsed.createTreeWalker(body, NodeFilter.SHOW_ELEMENT)
  while (walker.nextNode()) {
    const el = walker.currentNode
    if (DROP_TAGS.has(el.tagName)) {
      doomed.push(el)
      continue
    }
    for (const attr of Array.from(el.attributes)) {
      const name = attr.name.toLowerCase()
      if (name.startsWith('on')) {
        el.removeAttribute(attr.name)
        continue
      }
      if (URL_ATTRS.has(name) && /^\s*javascript:/i.test(attr.value)) {
        el.removeAttribute(attr.name)
      }
    }
  }
  for (const el of doomed) el.remove()

  return body.innerHTML
}

/** 解析 ``@media`` 这类带块的花括号配对，返回闭合花括号的下标。 */
function matchingBrace(css, openIndex) {
  let depth = 0
  for (let i = openIndex; i < css.length; i += 1) {
    if (css[i] === '{') depth += 1
    else if (css[i] === '}') {
      depth -= 1
      if (depth === 0) return i
    }
  }
  return css.length - 1
}

function scopeSelectorList(list, scope) {
  return list
    .split(',')
    .map((raw) => raw.trim())
    .filter(Boolean)
    .map((selector) => {
      // 文档自己的根选择器：映射到容器本身，CSS 变量因此只作用于容器内部
      if (/^(?::root|html|body)$/i.test(selector)) return scope
      // `html body` / `body.x` 这类以根开头的组合：去掉根，只留后半段
      if (/^(?:html|body)(?=[\s.>+~:])/i.test(selector)) {
        const rest = selector.replace(/^(?:html|body)/i, '').trim()
        return rest ? `${scope} ${rest}` : scope
      }
      if (selector === '*') return `${scope} *`
      return `${scope} ${selector}`
    })
    .join(', ')
}

/** 给一段 CSS 的所有选择器加上作用域前缀（``@media`` 递归，``@font-face`` 等原样保留）。 */
export function scopeCss(css, scope = '.research-doc') {
  // 注释里可能有 `}`，先去掉再按花括号分段，否则配平会错位
  const source = (css || '').replace(/\/\*[\s\S]*?\*\//g, '')
  let out = ''
  let cursor = 0

  while (cursor < source.length) {
    const open = source.indexOf('{', cursor)
    if (open === -1) {
      out += source.slice(cursor)
      break
    }
    const prelude = source.slice(cursor, open)
    const keyword = prelude.trim()
    const close = matchingBrace(source, open)

    if (keyword.startsWith('@')) {
      if (/^@(media|supports|layer|container)\b/i.test(keyword)) {
        out += `${prelude}{${scopeCss(source.slice(open + 1, close), scope)}}`
      } else {
        // @font-face / @keyframes / @charset / @import：内部不是选择器，原样保留
        out += source.slice(cursor, close + 1)
      }
    } else {
      out += `${scopeSelectorList(keyword, scope)}{${source.slice(open + 1, close)}}`
    }
    cursor = close + 1
  }

  return out
}

/**
 * 组装嵌入用的 HTML：带头部 <style>（已作用域化）与清洗后的正文。
 *
 * 样式放进正文里而不是 document.head，是为了让**预渲染**直接把它写进快照 ——
 * 页面不执行 JS 时也带着自己的排版。
 */
export function buildEmbeddedDoc({ body, styles }, scope = 'research-doc') {
  const css = (styles || []).map((block) => scopeCss(block, `.${scope}`)).join('\n')
  return `${css ? `<style>${css}</style>` : ''}${sanitizeBody(body)}`
}

/** 正文里是否已经有 h1（文档通常自带标题，页面就不再重复一个）。 */
export function hasHeading(body) {
  return /<h1[\s>]/i.test(body || '')
}
