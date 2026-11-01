/* =========================================================================
 * local-engine.js —— 浏览器内置检索与存储引擎
 *
 * 目的：让前端**不依赖后端、不依赖令牌**也能真正干活。
 *   · 数据存 localStorage，导入即落盘，刷新不丢
 *   · 中文 2-gram + 英文词 + TF-IDF 打分检索（不需要联网、不需要模型）
 *   · 从原文里抽取 时间 / 地点 / 对象 / 要求 / 联系人（值一律是原文子串）
 *   · 输出结构对齐 member-d 的 POST /v1/query，视图层代码零改动
 *
 * 诚实边界：这是**词面检索 + 规则抽取**，不是大模型语义理解。
 * 同义词靠 EXTEND 表兜底，跨句推理做不到；界面上会如实标注。
 * ========================================================================= */

const MSG_KEY = 'qunyi.local.messages.v1';
const SESS_KEY = 'qunyi.local.sessions.v1';
const MAX_MESSAGES = 2000;

/* ------------------------------------------------------------ 停用词 */
const STOP = new Set(
  ('的 了 吗 呢 啊 吧 呀 是 在 有 和 与 及 或 我 你 他 她 它 这 那 哪 什么 怎么 怎样 多少 几 请问 一下 那个 这个 一个 我们 你们 他们 要 会 就 都 也 还 去 来 上 下 中 个 把 被 给 对 从 到 说 问 想 知道 记得 之前 现在 具体 相关 关于 通知 消息 群 里 的 和')
    .split(' ')
    .filter(Boolean),
);

/* ------------------------------------------------ 同义词 / 口语扩展 */
const EXTEND = {
  集合: ['集合', '集合点', '集合地', '签到', '集合时间', '在哪里', '地点'],
  地点: ['地点', '在哪里', '集合', '位置', '场地', '教室', '地点在哪'],
  时间: ['时间', '几点', '什么时候', '几点开始', '日期'],
  要求: ['要求', '需要带', '带什么', '携带', '注意', '准备'],
  谁: ['谁', '联系人', '负责人', '联系'],
  截止: ['截止', 'deadline', '最后', '之前', '报名截止'],
  报名: ['报名', '登记', '报名表', '填表'],
  考试: ['考试', '期末', '期中', '考场', '补考'],
  讲座: ['讲座', '报告', '分享会', '宣讲'],
  志愿: ['志愿', '志愿服务', '义工', '公益'],
};

/* ------------------------------------------------------------ 工具 */
function uid(prefix) {
  return prefix + '_' + Date.now().toString(36) + Math.random().toString(36).slice(2, 8);
}

function load(key, fallback) {
  try {
    const raw = localStorage.getItem(key);
    return raw ? JSON.parse(raw) : fallback;
  } catch {
    return fallback;
  }
}

function save(key, value) {
  try {
    localStorage.setItem(key, JSON.stringify(value));
    return true;
  } catch (err) {
    return false;
  }
}

/** 中文按 2-gram + 单字，英文数字按词 */
export function tokenize(text) {
  const s = String(text || '').toLowerCase();
  const out = [];
  const latin = /[a-z0-9_]{2,}/g;
  let m;
  while ((m = latin.exec(s))) out.push(m[0]);

  const cjkOnly = s.replace(/[a-z0-9_]+/g, ' ');
  const cjk = /[\u4e00-\u9fa5]+/g;
  while ((m = cjk.exec(cjkOnly))) {
    const seg = m[0];
    for (let i = 0; i < seg.length; i++) {
      const ch = seg[i];
      if (!STOP.has(ch)) out.push(ch);
      if (i + 1 < seg.length) {
        const bi = seg.slice(i, i + 2);
        if (!STOP.has(bi)) out.push(bi);
      }
    }
  }
  return out;
}

function expandTokens(tokens) {
  const extra = [];
  tokens.forEach((t) => {
    const group = EXTEND[t];
    if (group) group.forEach((w) => extra.push(w));
  });
  return extra.length ? tokens.concat(extra.flatMap((w) => tokenize(w))) : tokens;
}

function splitSentences(text) {
  return String(text || '')
    .split(/[。！？；\n\r]+/)
    .map((s) => s.trim())
    .filter((s) => s.length >= 4);
}

function fmtDate(iso) {
  if (!iso) return '';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return '';
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
}

/* ------------------------------------------------------ 字段抽取规则 */
const FIELD_RULES = [
  {
    name: '时间',
    patterns: [
      /(\d{1,2}月\d{1,2}日(?:[（(]?周[一二三四五六日天][）)]?)?(?:[^\s，。；]{0,10}?(?:\d{1,2}[:：]\d{2}|上午|下午|晚上|中午|早上))?)/,
      /((?:今天|明天|后天|本周[一二三四五六日天]|下周[一二三四五六日天])(?:[^\s，。；]{0,10}?(?:\d{1,2}[:：]\d{2}|上午|下午|晚上|中午))?)/,
      /((?:上午|下午|晚上|中午|早上)\s*\d{1,2}[:：]\d{2})/,
      /(\d{1,2}[:：]\d{2}\s*(?:集合|开始|签到|出发)?)/,
    ],
  },
  {
    name: '地点',
    patterns: [
      /(?:在|到|于|去)\s*([\u4e00-\u9fa5A-Za-z0-9]{2,16}?(?:门口|广场|报告厅|教室|体育馆|图书馆|食堂|中心|楼|馆|场|室|站|厅))/,
      /([\u4e00-\u9fa5A-Za-z0-9]{2,14}(?:门口|广场|报告厅|体育馆|图书馆|食堂|教学楼|实验楼|宿舍楼下))\s*(?:集合|签到)/,
    ],
  },
  {
    name: '对象',
    patterns: [/((?:20\d{2}级|19\d{2}级)?[\u4e00-\u9fa5]{0,6}(?:全体|各班|全班|同学|学生|志愿者|新生|干事))(?=[，。；、\s]|集合|请|于)/],
  },
  {
    name: '要求',
    patterns: [
      /(?:请|需要|务必|记得|要求)?\s*((?:携带|带上|带好|自备|穿着|穿)[^，。；\n]{2,20})/,
      /(?:请勿|不得|禁止|不要)([^，。；\n]{2,18})/,
    ],
  },
  {
    name: '联系人',
    patterns: [/(?:联系|咨询|找|负责人[：: ]?)\s*([\u4e00-\u9fa5]{2,4})(?=[，。；、\s]|电话|微信|$)/, /(1[3-9]\d{9})/],
  },
  {
    name: '截止',
    patterns: [/((?:报名|提交|填表|申请)[^，。；\n]{0,6}(?:截止|截至)[^，。；\n]{0,16})/, /(截止(?:时间|日期)?[^，。；\n]{0,16})/],
  },
];

function extractFields(content) {
  const found = [];
  const seen = new Set();
  FIELD_RULES.forEach((rule) => {
    for (const re of rule.patterns) {
      const m = re.exec(content);
      if (!m) continue;
      const value = (m[1] || m[0] || '').replace(/[，。；、\s]+$/, '').trim();
      if (!value || value.length < 2) continue;
      if (!content.includes(value)) continue; // 必须逐字出现在原文
      const k = rule.name + '|' + value;
      if (seen.has(k)) continue;
      seen.add(k);
      found.push({ name: rule.name, value });
      break;
    }
  });
  return found;
}

/* ------------------------------------------- 索引侧同义扩展（提高召回）
 * 原文写“逾期未确认”，用户问“截止到哪天”，字面一个词都不重合。
 * 建索引时给文档补一个代表词，让这类同义提问也能命中；阈值不变。 */
const DOC_SYNONYMS = {
  截止: ['逾期', '过期', '报名截止', '截止日期', '前完成', '最后期限'],
  报名: ['报名表', '登记', '填表', '填写表单'],
  集合: ['签到', '集合点', '集合地'],
  考试: ['期末', '期中', '补考', '考场'],
};

/* ------------------------------------------------------------ 示例数据 */
export const SAMPLE_MESSAGES = [
  {
    group_label: '2024级计科1班群',
    sent_at: '2026-10-18T08:12:00.000Z',
    content:
      '【志愿服务】本周六（10月24日）上午8:30在图书馆东门集合，前往京口区敬老院开展志愿服务，预计中午11:30返校。请全体志愿者穿校服、携带学生证，不要迟到。带队负责人：李明，联系电话13812345678。',
    metadata: { event_key: 'service', title: '敬老院志愿服务' },
  },
  {
    group_label: '2024级计科1班群',
    sent_at: '2026-10-22T13:40:00.000Z',
    content:
      '【更正】关于敬老院志愿服务：集合时间改为上午9:00，集合地点不变，仍在图书馆东门。请互相转告，其他安排不变。',
    metadata: { event_key: 'service', title: '敬老院志愿服务（时间更正）', revision_kind: 'correction' },
  },
  {
    group_label: '教务处通知群',
    sent_at: '2026-10-20T02:00:00.000Z',
    content:
      '关于2026-2027学年第一学期期末考试安排的通知：考试定于1月5日至1月12日进行，考场安排将在考前一周发布于教务系统。请各位同学于12月20日前完成考试确认，逾期未确认视为放弃。咨询请联系教务处王老师，电话0511-88780000。',
    metadata: { event_key: 'exam', title: '期末考试安排' },
  },
  {
    group_label: '学院讲座群',
    sent_at: '2026-10-25T06:30:00.000Z',
    content:
      '【学术讲座】10月28日（周三）下午14:00在图书馆报告厅举办"人工智能与校园生活"讲座，主讲人为计算机学院张教授。讲座可计入第二课堂学分，请提前15分钟入场，携带校园卡签到，现场禁止饮食。',
    metadata: { event_key: 'lecture', title: '人工智能讲座' },
  },
];

/* ================================================================ 引擎 */
export class LocalEngine {
  constructor() {
    this.messages = load(MSG_KEY, []);
    this.sessions = load(SESS_KEY, {});
    this._idx = null;
  }

  /* ------------------------------------------------------------ 存储 */
  persist() {
    const ok = save(MSG_KEY, this.messages);
    save(SESS_KEY, this.sessions);
    return ok;
  }

  rebuild() {
    const docs = this.messages.map((m) => {
      const text = [m.content, m.group_label || '', (m.metadata && m.metadata.title) || ''].join(' ');
      const toks = expandTokens(tokenize(text));
      const tf = Object.create(null);
      toks.forEach((t) => (tf[t] = (tf[t] || 0) + 1));
      // 同义扩展：文档里出现“逾期”，就等于也写了“截止”
      Object.entries(DOC_SYNONYMS).forEach(([canon, alts]) => {
        if (alts.some((a) => text.includes(a))) tf[canon] = (tf[canon] || 0) + 0.9;
      });
      return { id: m.message_id, tf, len: toks.length || 1 };
    });
    const df = Object.create(null);
    docs.forEach((d) => Object.keys(d.tf).forEach((t) => (df[t] = (df[t] || 0) + 1)));
    const N = docs.length || 1;
    const idf = Object.create(null);
    Object.keys(df).forEach((t) => (idf[t] = Math.log(1 + N / df[t])));
    const avgLen = docs.reduce((s, d) => s + d.len, 0) / (docs.length || 1);
    this._idx = { docs, idf, avgLen: avgLen || 1 };
    return this._idx;
  }

  get index() {
    if (!this._idx) this.rebuild();
    return this._idx;
  }

  /* ------------------------------------------------- 后端同构接口 */
  capabilities() {
    return Promise.resolve({
      version: '1.0-local',
      mode: 'local',
      simulation: true,
      search: 'tfidf_zh_bigram',
      storage: 'localStorage',
      features: { import: true, query: true, export: true, session: true, backend: false },
    });
  }

  coverage() {
    const groups = [...new Set(this.messages.map((m) => m.group_label || '未标注群').filter(Boolean))];
    const dates = this.messages.map((m) => m.sent_at).filter(Boolean).sort();
    return Promise.resolve({
      imported_count: this.messages.length,
      indexed_count: this.messages.length,
      groups,
      scope: dates.length ? `${fmtDate(dates[0])} ~ ${fmtDate(dates[dates.length - 1])}` : '尚未导入数据',
      mode: 'local',
    });
  }

  stats() {
    const groups = new Set(this.messages.map((m) => m.group_label || '未标注群'));
    const dates = this.messages.map((m) => m.sent_at).filter(Boolean).sort();
    return {
      count: this.messages.length,
      groups: groups.size,
      from: dates.length ? fmtDate(dates[0]) : '',
      to: dates.length ? fmtDate(dates[dates.length - 1]) : '',
    };
  }

  listMessages({ limit = 20, offset = 0 } = {}) {
    const sorted = this.messages.slice().sort((a, b) => (b.imported_at || '').localeCompare(a.imported_at || ''));
    return Promise.resolve({
      items: sorted.slice(offset, offset + limit),
      total: this.messages.length,
      limit,
      offset,
    });
  }

  getMessage(id) {
    const m = this.messages.find((x) => x.message_id === id);
    if (!m) return Promise.reject(this._err(404, 'NOT_FOUND', '消息不存在'));
    return Promise.resolve(m);
  }

  importMessages(payload) {
    const items = (payload && payload.messages) || [];
    const errors = [];
    let success = 0;
    if (items.length > 200) {
      return Promise.reject(this._err(413, 'BODY_TOO_LARGE', '单次最多 200 条'));
    }
    items.forEach((item, i) => {
      const content = String((item && item.content) || '').trim();
      if (!content) {
        errors.push({ index: i, code: 'INVALID_INPUT', message: '正文不能为空' });
        return;
      }
      if (content.length > 20000) {
        errors.push({ index: i, code: 'INVALID_INPUT', message: '正文超过 20000 字' });
        return;
      }
      const meta = item.metadata ? { ...item.metadata } : null;
      if (meta && meta.fields) {
        const bad = Object.entries(meta.fields).filter(([, v]) => v && !content.includes(v));
        if (bad.length) {
          errors.push({ index: i, code: 'INVALID_INPUT', message: `字段值未逐字出现在原文：${bad.map(([k]) => k).join('、')}` });
          return;
        }
      }
      this.messages.push({
        message_id: uid('m'),
        seq: this.messages.length + 1,
        content,
        group_label: (item.group_label || '').trim() || '未标注群',
        sent_at: item.sent_at || null,
        imported_at: new Date().toISOString(),
        metadata: meta || {},
      });
      success += 1;
    });
    if (this.messages.length > MAX_MESSAGES) {
      this.messages = this.messages.slice(-MAX_MESSAGES);
    }
    this._idx = null;
    const ok = this.persist();
    if (!ok) {
      return Promise.reject(this._err(507, 'STORAGE_FULL', '浏览器本地存储已满，请删除部分数据后再导入'));
    }
    return Promise.resolve({
      batch_id: uid('b'),
      success_count: success,
      failure_count: errors.length,
      errors,
      replayed: false,
    });
  }

  patchMessage(id, patch) {
    const m = this.messages.find((x) => x.message_id === id);
    if (!m) return Promise.reject(this._err(404, 'NOT_FOUND', '消息不存在'));
    if (patch.content !== undefined) m.content = String(patch.content).trim();
    if (patch.group_label !== undefined) m.group_label = patch.group_label || '未标注群';
    if (patch.sent_at !== undefined) m.sent_at = patch.sent_at || null;
    if (patch.metadata !== undefined) m.metadata = patch.metadata || {};
    m.updated_at = new Date().toISOString();
    this._idx = null;
    this.invalidateSessions();
    this.persist();
    return Promise.resolve(m);
  }

  deleteMessage(id) {
    const i = this.messages.findIndex((x) => x.message_id === id);
    if (i < 0) return Promise.reject(this._err(404, 'NOT_FOUND', '消息不存在'));
    this.messages.splice(i, 1);
    this._idx = null;
    this.invalidateSessions();
    this.persist();
    return Promise.resolve({ deleted: id });
  }

  rebuildIndex() {
    this.rebuild();
    return Promise.resolve({ indexed: this.messages.length });
  }

  events() {
    const map = new Map();
    this.messages.forEach((m) => {
      const k = (m.metadata && m.metadata.event_key) || '';
      if (!k) return;
      const cur = map.get(k) || { event_key: k, title: (m.metadata && m.metadata.title) || k, count: 0, group_label: m.group_label };
      cur.count += 1;
      map.set(k, cur);
    });
    return Promise.resolve({ events: [...map.values()] });
  }

  invalidateSessions() {
    Object.keys(this.sessions).forEach((k) => delete this.sessions[k]);
  }

  /* ---------------------------------------------------------- 查询 */
  query(payload) {
    const question = String((payload && payload.question) || '').trim();
    if (!question) return Promise.reject(this._err(400, 'INVALID_INPUT', 'question 不能为空'));
    if (!this.messages.length) {
      return Promise.resolve({
        status: 'not_found',
        answer: '本地空间还没有数据。先在上面粘贴群通知导入，再来提问。',
        citations: [],
        fields: [],
        conditions: {},
        condition_version: 0,
        session_id: uid('s'),
        search_mode: payload.search_mode || 'expanded',
        trace: [],
        coverage: { imported_count: 0 },
        request_id: uid('req'),
        ai: { mode: 'local', simulation: true, calls: 0, actual_participation: true, succeeded: true },
      });
    }

    /* --- 会话与条件 --- */
    const sid = (payload.session_id && this.sessions[payload.session_id] ? payload.session_id : null) || uid('s');
    const sess = this.sessions[sid] || { id: sid, version: 0, conditions: {}, turns: [], created: new Date().toISOString() };
    const conds = { ...(sess.conditions || {}) };
    if (payload.condition_updates) Object.assign(conds, payload.condition_updates);
    if (Array.isArray(payload.clear_conditions)) payload.clear_conditions.forEach((k) => delete conds[k]);
    conds.query_text = question;
    sess.version = (sess.version || 0) + 1;
    sess.conditions = conds;

    /* --- 候选集 --- */
    let pool = this.messages;
    if (conds.event_key) {
      const narrowed = pool.filter((m) => (m.metadata && m.metadata.event_key) === conds.event_key);
      if (narrowed.length) pool = narrowed;
    }
    if (conds.group_label) {
      const narrowed = pool.filter((m) => (m.group_label || '').includes(conds.group_label));
      if (narrowed.length) pool = narrowed;
    }
    if (conds.sent_after) pool = pool.filter((m) => !m.sent_at || m.sent_at >= conds.sent_after);
    if (conds.sent_before) pool = pool.filter((m) => !m.sent_at || m.sent_at <= conds.sent_before);

    /* --- 打分 --- */
    const rawTokens = [...new Set(tokenize(question))];
    const qTokens = [...new Set(expandTokens(rawTokens))];
    // 只把「主查询词」里的 2 字词当命中门槛，避免“前/到/天”这类单字到处乱撞
    const qMain = [...new Set(rawTokens.filter((t) => t.length >= 2))];
    const { docs, idf, avgLen } = this.index;
    const docMap = new Map(docs.map((d) => [d.id, d]));

    const scored = pool
      .map((m) => {
        const d = docMap.get(m.message_id);
        if (!d) return { m, score: 0, hit: [], hitMain: 0 };
        let score = 0;
        const hit = [];
        qTokens.forEach((t) => {
          const tf = d.tf[t];
          if (!tf) return;
          const w = idf[t] || 0.3;
          const boost = t.length >= 2 ? 1.35 : 1; // 2-gram 比单字更可信
          score += w * boost * (1 + Math.log(tf));
          hit.push(t);
        });
        // 长度归一，避免长文占便宜
        score = score / (0.7 + 0.3 * (d.len / avgLen));
        // 标题/事件名命中额外加权
        const title = ((m.metadata && m.metadata.title) || '') + (m.group_label || '');
        if (title && qTokens.some((t) => t.length >= 2 && title.includes(t))) score *= 1.2;
        const hitMain = qMain.filter((t) => d.tf[t]).length;
        return { m, score, hit: [...new Set(hit)], hitMain };
      })
      .filter((x) => x.score > 0)
      .sort((a, b) => b.score - a.score);

    /* 覆盖率门槛：主查询词至少要被真实命中一部分，否则宁可说“没找到”，
       也不要拿一条不相关的通知糊弄用户。 */
    const coverOf = (x) => (qMain.length ? x.hitMain / qMain.length : 1);
    const qualified = scored.filter((x) => x.hitMain > 0 && (coverOf(x) >= 0.15 || x.hitMain >= 3));

    const top = qualified.slice(0, 5);
    const best = top[0];
    const maxScore = best ? best.score : 0;
    const coverage = { imported_count: this.messages.length, scope: '浏览器本地空间' };

    /* --- 澄清：口语指代 + 多个并列候选 --- */
    const vague = /那个|那次|之前|上次|前几天|那个活动|叫什么/.test(question);
    const distinctEvents = [...new Set(top.map((t) => (t.m && t.m.metadata && t.m.metadata.event_key) || t.m.group_label))];
    if (vague && distinctEvents.length >= 2 && top.length >= 2) {
      const options = distinctEvents.slice(0, 4).map((k) => {
        const t = top.find((x) => ((x.m.metadata && x.m.metadata.event_key) || x.m.group_label) === k);
        return {
          event_key: (t.m.metadata && t.m.metadata.event_key) || k,
          title: (t.m.metadata && t.m.metadata.title) || t.m.group_label || k,
        };
      });
      const result = {
        status: 'clarify',
        question: '你说的可能是下面这几个，点一个我再看细节：',
        options,
        conditions: conds,
        condition_version: sess.version,
        session_id: sid,
        search_mode: payload.search_mode || 'expanded',
        trace: [{ tool: 'search', count: top.length, search: 'tfidf' }],
        coverage,
        request_id: uid('req'),
        ai: { mode: 'local', simulation: true, calls: 0, actual_participation: true, succeeded: true },
      };
      this._rememberTurn(sess, question, result);
      return Promise.resolve(result);
    }

    if (!best || maxScore < 1.2) {
      const result = {
        status: 'not_found',
        answer: `在已导入的 ${this.messages.length} 条消息里没有找到相关原文。换个说法，或先导入包含这件事的群通知。`,
        citations: [],
        fields: [],
        suggestions: scored.slice(0, 3).map((t) => ({
          message_id: t.m.message_id,
          group_label: t.m.group_label,
          title: (t.m.metadata && t.m.metadata.title) || t.m.group_label || '这条原文',
          snippet: t.m.content.slice(0, 70),
        })),
        conditions: conds,
        condition_version: sess.version,
        session_id: sid,
        search_mode: payload.search_mode || 'expanded',
        trace: [{ tool: 'search', count: 0, search: 'tfidf' }],
        coverage,
        request_id: uid('req'),
        ai: { mode: 'local', simulation: true, calls: 0, actual_participation: true, succeeded: true },
      };
      this._rememberTurn(sess, question, result);
      return Promise.resolve(result);
    }

    /* --- 组装答案 --- */
    const cited = top.filter((t) => t.score >= maxScore * 0.45).slice(0, 3);
    // 同一事件有多版本时，时间最新的那条优先作为主证据（模拟“更正覆盖”）
    const primary = cited.slice().sort((a, b) => {
      const ka = (a.m.metadata && a.m.metadata.event_key) || '';
      const kb = (b.m.metadata && b.m.metadata.event_key) || '';
      if (ka && ka === kb) return (b.m.sent_at || '').localeCompare(a.m.sent_at || '');
      return b.score - a.score;
    })[0] || best;

    const fields = extractFields(primary.m.content).map((f) => ({
      name: f.name,
      value: f.value,
      quote: this._quoteAround(primary.m.content, f.value),
      message_id: primary.m.message_id,
    }));

    const sentences = splitSentences(primary.m.content);
    const qset = new Set(qTokens.filter((t) => t.length >= 2));
    let core = sentences
      .map((s) => ({ s, n: [...qset].filter((t) => s.includes(t)).length }))
      .sort((a, b) => b.n - a.n)[0];
    if (!core || !core.n) core = { s: sentences[0] || primary.m.content.slice(0, 80) };

    const when = fmtDate(primary.m.sent_at);
    const head = `根据「${primary.m.group_label || '未标注群'}」${when ? when + ' ' : ''}的通知：`;
    let answer = head + core.s.trim() + (/(。|！|？)$/.test(core.s.trim()) ? '' : '。');

    if (fields.length) {
      const brief = fields
        .filter((f) => f.name !== '对象')
        .slice(0, 3)
        .map((f) => `${f.name}：${f.value}`)
        .join('；');
      if (brief) answer += `\n关键信息 —— ${brief}。`;
    }
    if (cited.length > 1 && primary.m.metadata && primary.m.metadata.revision_kind === 'correction') {
      answer += '\n注意：这条是对前面通知的更正，上面取的是更正后的版本。';
    }

    const citations = cited.map((t) => ({
      message_id: t.m.message_id,
      quote: this._quoteAround(t.m.content, (t.hit.find((h) => h.length >= 2) || '') || ''),
      group_label: t.m.group_label,
      sent_at: t.m.sent_at,
      score: Number(t.score.toFixed(2)),
    }));

    const result = {
      status: 'answer',
      answer,
      fields,
      citations,
      conditions: conds,
      condition_version: sess.version,
      session_id: sid,
      search_mode: payload.search_mode || 'expanded',
      trace: [{ tool: 'search', count: scored.length, search: 'tfidf_zh_bigram' }],
      coverage,
      request_id: uid('req'),
      ai: { mode: 'local', simulation: true, calls: 0, actual_participation: true, succeeded: true },
    };
    this._rememberTurn(sess, question, result);
    return Promise.resolve(result);
  }

  _rememberTurn(sess, question, result) {
    sess.turns.push({ question, result, created: new Date().toISOString() });
    sess.turns = sess.turns.slice(-30);
    this.sessions[sess.id] = sess;
    this.persist();
  }

  _quoteAround(content, value) {
    if (!value) return content.length > 90 ? content.slice(0, 90) + '…' : content;
    const i = content.indexOf(value);
    if (i < 0) return content.length > 90 ? content.slice(0, 90) + '…' : content;
    const start = Math.max(0, i - 30);
    const end = Math.min(content.length, i + value.length + 40);
    return (start > 0 ? '…' : '') + content.slice(start, end) + (end < content.length ? '…' : '');
  }

  getSession(id) {
    const s = this.sessions[id];
    if (!s) return Promise.reject(this._err(404, 'NOT_FOUND', '会话不存在或已失效'));
    return Promise.resolve(s);
  }

  /* -------------------------------------------------------- 维护接口 */
  loadSample() {
    return this.importMessages({
      messages: SAMPLE_MESSAGES.map((m) => ({
        content: m.content,
        group_label: m.group_label,
        sent_at: m.sent_at,
        metadata: m.metadata,
      })),
    });
  }

  clearAll() {
    this.messages = [];
    this.sessions = {};
    this._idx = null;
    this.persist();
    return Promise.resolve({ cleared: true });
  }

  exportAll() {
    return {
      exported_at: new Date().toISOString(),
      mode: 'local',
      count: this.messages.length,
      messages: this.messages,
    };
  }

  /** 导入前的解析：纯文本 / 每行一条 / JSON 数组 */
  parseBulk(text) {
    const raw = String(text || '').trim();
    if (!raw) return { items: [], format: 'empty' };

    // JSON
    if (raw.startsWith('[') || raw.startsWith('{')) {
      try {
        const data = JSON.parse(raw);
        const arr = Array.isArray(data) ? data : data.messages || [];
        const items = arr
          .map((x) => (typeof x === 'string' ? { content: x } : x))
          .filter((x) => x && x.content)
          .map((x) => ({
            content: String(x.content),
            group_label: x.group_label || x.group || '',
            sent_at: x.sent_at || x.time || null,
            metadata: x.metadata || {},
          }));
        return { items, format: 'json' };
      } catch {
        /* 落回文本解析 */
      }
    }

    // 文本：空行分段（保留段内换行）
    const blocks = raw
      .split(/\n\s*\n/)
      .map((b) => b.trim())
      .filter(Boolean);

    const items = blocks.map((block) => {
      let group = '';
      let sentAt = null;
      let body = block;
      // 首行形如：[群名] 2026-10-20 08:00
      const firstLine = block.split('\n')[0];
      const tag = /^\s*[[【(（]([^\]】)）]{2,30})[\]】)）]\s*(.*)$/.exec(firstLine);
      if (tag) {
        group = tag[1].trim();
        body = block.replace(firstLine, tag[2] || '').trim() || block;
      }
      const dt = /(\d{4})[-/年](\d{1,2})[-/月](\d{1,2})日?\s*(\d{1,2})?:?(\d{2})?/.exec(body);
      if (dt) {
        const d = new Date(
          Number(dt[1]),
          Number(dt[2]) - 1,
          Number(dt[3]),
          dt[4] ? Number(dt[4]) : 9,
          dt[5] ? Number(dt[5]) : 0,
        );
        if (!Number.isNaN(d.getTime())) sentAt = d.toISOString();
      }
      return { content: body, group_label: group, sent_at: sentAt };
    });

    return { items: items.filter((x) => x.content && x.content.length >= 4), format: blocks.length > 1 ? 'blocks' : 'single' };
  }

  _err(status, code, message) {
    const e = new Error(message);
    e.status = status;
    e.code = code;
    return e;
  }
}
