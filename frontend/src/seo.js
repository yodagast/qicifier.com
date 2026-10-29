// head 里的东西**只能由这一份实现写入**。
//
// 为什么收敛成一个函数：预渲染（tools/prerender.py）是用浏览器跑同一套代码，
// 所以 applySeo 在预渲染时写进 HTML、在站内导航时改写 DOM —— 天然一致。
// 谁要是另写一份「构建时注入 meta」的逻辑，两边一定会漂移，
// 最典型的事故是：预渲染 HTML 里是 A 标题，用户进来后 JS 改成 B 标题。
import { SITE_ORIGIN, SITE_TITLE } from './site'

const SITE = SITE_TITLE

function setTag(selector, tag, attrs) {
  let el = document.head.querySelector(selector)
  if (!el) {
    el = document.createElement(tag)
    document.head.appendChild(el)
  }
  for (const [key, value] of Object.entries(attrs)) el.setAttribute(key, value)
  return el
}

/**
 * 写入本页的 title / description / canonical / OG / robots / JSON-LD。
 *
 * @param {object}  options
 * @param {string}  options.title       页面名（不含站名后缀；留空则只显示站名）
 * @param {string}  [options.description]
 * @param {string}  [options.path]      canonical 的站内路径。**必须传 to.path 而不是
 *                                      to.fullPath**：``?q=`` / ``?page=`` 会造出无限
 *                                      URL 空间（robots 已挡），canonical 也不能指向它们。
 * @param {object}  [options.jsonLd]    结构化数据；传 null 会移除上一页残留的那一段
 * @param {boolean} [options.noindex]   薄页 / 账户页：不索引但仍跟随链接
 */
export function applySeo({ title, description = '', path = '/', jsonLd = null, noindex = false }) {
  const url = SITE_ORIGIN + path
  document.title = title ? `${title} · ${SITE}` : SITE

  setTag('meta[name="description"]', 'meta', { name: 'description', content: description })
  setTag('link[rel="canonical"]', 'link', { rel: 'canonical', href: url })
  setTag('meta[property="og:title"]', 'meta', { property: 'og:title', content: title || SITE })
  setTag('meta[property="og:description"]', 'meta', { property: 'og:description', content: description })
  setTag('meta[property="og:url"]', 'meta', { property: 'og:url', content: url })
  setTag('meta[property="og:type"]', 'meta', { property: 'og:type', content: 'website' })
  setTag('meta[property="og:site_name"]', 'meta', { property: 'og:site_name', content: SITE })
  setTag('meta[name="robots"]', 'meta', {
    name: 'robots',
    content: noindex ? 'noindex, follow' : 'index, follow',
  })

  let script = document.head.querySelector('script[data-seo-jsonld]')
  if (jsonLd) {
    if (!script) {
      script = document.createElement('script')
      script.type = 'application/ld+json'
      script.dataset.seoJsonld = '1'
      document.head.appendChild(script)
    }
    script.textContent = JSON.stringify(jsonLd)
  } else if (script) {
    // 上一页的 JSON-LD 不能留到这一页 —— 那会让「企业页」带着「集群 CollectionPage」
    script.remove()
  }
}

const ORG_NAME = '杭州余杭波动粒子信息经营部'

/** 站点自身（``isPartOf`` / ``publisher`` 共用），避免各页各写一份名字。 */
export const SITE_ENTITY = {
  '@type': 'WebSite',
  name: '产业带企业库',
  alternateName: SITE_TITLE,
  url: SITE_ORIGIN,
}

const PUBLISHER = { '@type': 'Organization', name: ORG_NAME, url: `${SITE_ORIGIN}/company` }

/**
 * 集群页的 CollectionPage + ItemList。
 *
 * description 用页面已有的事实拼（产业 / 产区 / 家数 / 核实档位），不写模板空话：
 * 「已收录 128 家企业」这类可抽取的事实才是 AI 引擎要的东西。
 */
export function clusterJsonLd(cluster) {
  const url = `${SITE_ORIGIN}/clusters/${encodeURIComponent(cluster.slug)}`
  const primary = (cluster.regions || []).find((region) => region.is_primary) || (cluster.regions || [])[0]
  const items = (cluster.enterprises || []).slice(0, 20).map((enterprise, index) => ({
    '@type': 'ListItem',
    position: index + 1,
    name: enterprise.name,
    url: `${SITE_ORIGIN}/enterprises/${enterprise.id}`,
  }))

  return {
    '@context': 'https://schema.org',
    '@type': 'CollectionPage',
    name: cluster.name,
    description: clusterSeoDescription(cluster),
    url,
    inLanguage: 'zh-CN',
    isPartOf: SITE_ENTITY,
    publisher: PUBLISHER,
    ...(primary
      ? {
          about: {
            '@type': 'Place',
            name: primary.name,
            ...(primary.path ? { address: primary.path } : {}),
          },
        }
      : {}),
    mainEntity: {
      '@type': 'ItemList',
      numberOfItems: cluster.enterprise_count ?? items.length,
      itemListElement: items,
    },
  }
}

/** 集群页的 description：一句话说清「是什么、在哪、收了多少家、可信度如何」。 */
export function clusterSeoDescription(cluster) {
  return (
    [
      cluster.industry_label || cluster.industry_l1 || cluster.industry_l2,
      cluster.core_area && `${cluster.core_area}产区`,
      `已收录 ${cluster.enterprise_count ?? 0} 家企业`,
      cluster.data_grade_label,
    ]
      .filter(Boolean)
      .join('，') + '。来源与核实日期逐条标注。'
  )
}

/**
 * 企业页的 Organization。
 *
 * 两个坑：① 不要用 LocalBusiness —— 这是名录里的企业实体，不是我们的门店；
 * ② 集团站 / 母公司站的链接（PARENT_SITE_KINDS）不能塞进 url，会张冠李戴，宁可不填。
 */
export function enterpriseJsonLd(enterprise, { isParentSite = false } = {}) {
  const url = `${SITE_ORIGIN}/enterprises/${enterprise.id}`
  const address = [enterprise.address_registered || enterprise.address].filter(Boolean)[0]
  return {
    '@context': 'https://schema.org',
    '@type': 'Organization',
    name: enterprise.name,
    ...(enterprise.name_en ? { alternateName: enterprise.name_en } : {}),
    url,
    mainEntityOfPage: url,
    ...(!isParentSite && enterprise.website ? { sameAs: [enterprise.website] } : {}),
    ...(enterprise.public_phone ? { telephone: enterprise.public_phone } : {}),
    ...(enterprise.public_email ? { email: enterprise.public_email } : {}),
    address: {
      '@type': 'PostalAddress',
      addressCountry: 'CN',
      addressRegion: '浙江省',
      addressLocality: [enterprise.city, enterprise.district].filter(Boolean).join(''),
      ...(address ? { streetAddress: address } : {}),
    },
  }
}

/** 说明页的 Dataset：AI 引用时最认的类型之一。 */
export function datasetJsonLd(stats) {
  return {
    '@context': 'https://schema.org',
    '@type': 'Dataset',
    name: '浙江产业集群企业库',
    description:
      '以官方名单为骨架、逐条标注核实程度的浙江产业集群与企业数据。核实档位五级：官方认定 / 政府公开 / 媒体·协会 / 站方整理 / 企业自报。',
    url: `${SITE_ORIGIN}/about`,
    spatialCoverage: { '@type': 'Place', name: '浙江省' },
    temporalCoverage: String(new Date().getFullYear()),
    creator: PUBLISHER,
    publisher: PUBLISHER,
    isAccessibleForFree: true,
    ...(stats
      ? {
          variableMeasured: [
            `收录企业 ${stats.enterprise_total} 家`,
            `产业集群 ${stats.cluster_total} 个`,
            `县级行政区 ${stats.region_total} 个`,
          ],
        }
      : {}),
    distribution: {
      '@type': 'DataDownload',
      contentUrl: `${SITE_ORIGIN}/api/enterprises`,
      encodingFormat: 'application/json',
    },
  }
}

/**
 * 说明页用：把 Dataset 与 FAQPage 合成一个 ``@graph``。
 *
 * 一个页面只能挂一段 JSON-LD（applySeo 按 ``data-seo-jsonld`` 覆盖），
 * 所以两种类型要合进同一份，而不是互相覆盖。
 */
export function aboutJsonLd(stats) {
  const strip = (obj) => {
    const { '@context': _context, ...rest } = obj
    return rest
  }
  return {
    '@context': 'https://schema.org',
    '@graph': [strip(datasetJsonLd(stats)), strip(gradeFaqJsonLd())],
  }
}

/** 行业研究文档的 Article 结构化数据（正文嵌在站点页面里，但日志类型仍是文章）。 */
export function articleJsonLd(article) {
  const url = `${SITE_ORIGIN}/research/${encodeURIComponent(article.slug)}`
  return {
    '@context': 'https://schema.org',
    '@type': 'Article',
    headline: article.title,
    description: article.summary || '',
    url,
    mainEntityOfPage: url,
    datePublished: article.published_at || '',
    dateModified: article.updated_at || article.published_at || '',
    inLanguage: 'zh-CN',
    author: PUBLISHER,
    publisher: PUBLISHER,
    isPartOf: SITE_ENTITY,
  }
}

/** 核实档位说明页的 FAQPage（/about 的正文本身就是问答结构）。 */
export function gradeFaqJsonLd() {
  return {
    '@context': 'https://schema.org',
    '@type': 'FAQPage',
    mainEntity: [
      {
        '@type': 'Question',
        name: '本站的核实档位分几级？',
        acceptedAnswer: {
          '@type': 'Answer',
          text: '五级：官方认定（official）、政府公开信息（gov_open）、媒体·协会来源（media）、站方整理（site_curated）、企业自报（self_reported，本站未核验）。',
        },
      },
      {
        '@type': 'Question',
        name: '「站方整理」算官方认定吗？',
        acceptedAnswer: {
          '@type': 'Answer',
          text: '不算。站方整理指没有官方名单可依、由本站依据公开信息整理的条目（例如多数镇街级块状经济），单独标档，不与官方来源相加或比较。',
        },
      },
      {
        '@type': 'Question',
        name: '收录与排序收费吗？',
        acceptedAnswer: {
          '@type': 'Answer',
          text: '不收费。本站不收入驻费、不卖排名、不做付费认证与等级背书；排序只依据公开可核验程度与资料完整度，对所有企业一致。',
        },
      },
      {
        '@type': 'Question',
        name: '不同名单的家数可以直接相加吗？',
        acceptedAnswer: {
          '@type': 'Answer',
          text: '不能。各名单发布主体、认定层级、时点不同，本站互不相加、互不比较；页面上的数字都标注了来源、口径与核验日期。',
        },
      },
    ],
  }
}
