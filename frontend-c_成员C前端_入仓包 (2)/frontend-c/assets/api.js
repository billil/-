/* =========================================================================
 * api.js —— 群忆后端 API 客户端
 * 严格对齐 member-d/backend 的实际实现（qunyi/service.py + qunyi/server.py）
 *
 * 后端约束（前端必须遵守，否则会报错）：
 *   1. 除 GET /health 外，全部接口需要 Authorization: Bearer <token>
 *   2. POST / PATCH 的 Content-Type 必须是 application/json，请求体 <= 1 MiB
 *   3. 后端不带 CORS 头、不处理 OPTIONS，前端必须同源访问（serve.py 代理 /api）
 *   4. 错误响应形如 {status:'error', error:{code,message}, request_id}
 *   5. 修/删消息会让「整个数据空间」的会话全部失效，旧 session 再查返回 404
 * ========================================================================= */

export const DEFAULT_BASE = '/gw';
// 分享链接里的 sharecode（公网网关按它放行每个请求）；本地开发没有它，为空即可。
const SHARECODE = new URLSearchParams(location.search).get('sharecode') || '';
export const BASE_KEY = 'qunyi.base';
export const TOKEN_KEY = 'qunyi.token';       // sessionStorage：关标签页即失效
export const KEEP_KEY = 'qunyi.keepToken';    // localStorage：用户明确勾选才记住

export class ApiError extends Error {
  constructor({ status = 0, code = 'UNKNOWN', message = '请求失败', requestId = '', payload = null }) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
    this.code = code;
    this.requestId = requestId;
    this.payload = payload;
  }

  /** 给用户看的一句话解释（错误码 ≠ 没有数据） */
  get hint() {
    const table = {
      UNAUTHORIZED: '令牌缺失或无效：请在右上角「连接设置」里填 state/credentials.json 中的 key。',
      NOT_FOUND: '对象不存在，或已被删除 / 不属于当前数据空间。',
      IDEMPOTENCY_CONFLICT: '同一幂等键提交了不同内容（409）。换一个幂等键再试。',
      BODY_TOO_LARGE: '请求超过 1 MiB，请拆分成多批导入。',
      JSON_REQUIRED: '后端要求 application/json（本客户端会自动带上）。',
      INVALID_JSON: '请求体格式不正确。',
      INVALID_INPUT: '有字段不合规，详见返回信息。',
      MODEL_NOT_CONFIGURED: '后端处于 live 模式但没配模型（503），不是「没有数据」。',
      MODEL_TIMEOUT: '模型调用超时（504）。',
      QUERY_DEADLINE: '本次查询超过总时限（504）。',
      MODEL_TOOL_DENIED: '模型越界调用了不允许的工具或证据（502）。',
      UNSUPPORTED_EVIDENCE: '模型给出的字段无法被原文支持（502）。',
      STALE_EVIDENCE: '模型引用了过期版本的字段（502）。',
      CONFLICTING_EVIDENCE: '相关事件存在冲突字段，需人工核实（502）。',
      NOT_IMPLEMENTED: '该接口尚未在当前后端实现（404）。这不是故障——功能开关会在后端上线后自动可用。',
      BACKEND_UNREACHABLE: '代理连不上后端，请确认 member-d/backend 里已运行 python -m qunyi。',
      NETWORK: '网络中断或后端未启动。',
      TIMEOUT: '等待响应超时。',
      BAD_RESPONSE: '后端返回了非 JSON 内容（可能命中了别的服务）。',
    };
    return table[this.code] || '';
  }
}

export class Api {
  constructor() {
    // 老版本把 /api 存在 localStorage 里；公网网关会拦 /api（只放行 /api/health），
    // 因此统一迁移到 /gw，避免老浏览器缓存把请求打回被拦的路径。
    const saved = localStorage.getItem(BASE_KEY);
    this.base = !saved || saved === '/api' ? DEFAULT_BASE : saved;
    const keep = localStorage.getItem(KEEP_KEY) === '1';
    this.token = (keep ? localStorage.getItem(TOKEN_KEY) : sessionStorage.getItem(TOKEN_KEY)) || '';
    // 服务端令牌模式：公开演示环境把空间令牌留在服务端（serve.py 通过环境变量注入），
    // 浏览器里不保存、地址栏也不出现任何令牌。由 app.js 探测成功后置为 true。
    this.serverAuth = false;
    // 本地模式：没有后端也能用。app.js 在探测不到后端时挂上 LocalEngine，
    // 此后所有读写都走浏览器本地存储 + 本地检索，接口形状与后端一致。
    this.local = null;
  }

  get isLocal() {
    return Boolean(this.local);
  }

  get configured() {
    return Boolean(this.local) || Boolean(this.token) || this.serverAuth;
  }

  /**
   * 组装请求 URL。公网网关要求每个请求都带 sharecode（Cookie 不算），
   * 所以从进入页面的地址里取一次，之后所有 API 请求都自动带上。
   */
  url(path) {
    const u = this.base + path;
    return SHARECODE ? u + (u.includes('?') ? '&' : '?') + 'sharecode=' + SHARECODE : u;
  }

  /** 保存连接配置；remember=true 时令牌写入 localStorage（仅本机演示用） */
  saveConfig({ base, token, remember }) {
    this.base = (base || DEFAULT_BASE).replace(/\/+$/, '') || DEFAULT_BASE;
    this.token = token || '';
    localStorage.setItem(BASE_KEY, this.base);
    localStorage.setItem(KEEP_KEY, remember ? '1' : '0');
    if (remember) {
      localStorage.setItem(TOKEN_KEY, this.token);
      sessionStorage.removeItem(TOKEN_KEY);
    } else {
      sessionStorage.setItem(TOKEN_KEY, this.token);
      localStorage.removeItem(TOKEN_KEY);
    }
  }

  clearToken() {
    this.token = '';
    sessionStorage.removeItem(TOKEN_KEY);
    localStorage.removeItem(TOKEN_KEY);
    localStorage.setItem(KEEP_KEY, '0');
  }

  /**
   * 统一请求。返回解析后的 JSON；非 2xx 抛 ApiError。
   * @param {string} method
   * @param {string} path 形如 /v1/messages
   * @param {object|undefined} body
   */
  async call(method, path, body, options = {}) {
    const { timeout = 45000 } = options;
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), timeout);
    const headers = {};
    // 令牌走自定义头，而不是 Authorization：云端网关会把 Authorization 当成它自己的
    // 鉴权头直接拒掉（401 空响应体）。serve.py 收到 X-Qunyi-Token 后再转成
    // Authorization: Bearer 送给后端，后端契约因此保持不变。
    if (this.token) headers['X-Qunyi-Token'] = this.token;

    let payload;
    if (body !== undefined) {
      headers['Content-Type'] = 'application/json';
      payload = JSON.stringify(body);
    }

    let res;
    try {
      res = await fetch(this.url(path), {
        method,
        headers,
        body: payload,
        signal: controller.signal,
        cache: 'no-store',
        credentials: 'omit',
      });
    } catch (err) {
      const aborted = err && err.name === 'AbortError';
      throw new ApiError({
        status: 0,
        code: aborted ? 'TIMEOUT' : 'NETWORK',
        message: aborted ? '请求超时' : '无法连接后端',
        payload: String(err && err.message),
      });
    } finally {
      clearTimeout(timer);
    }

    const text = await res.text();
    let data = null;
    if (text) {
      try {
        data = JSON.parse(text);
      } catch {
        throw new ApiError({
          status: res.status,
          code: 'BAD_RESPONSE',
          message: '返回内容不是 JSON',
          payload: text.slice(0, 300),
        });
      }
    }

    if (!res.ok) {
      const e = (data && data.error) || {};
      throw new ApiError({
        status: res.status,
        code: e.code || 'HTTP_' + res.status,
        message: e.message || '请求失败（HTTP ' + res.status + '）',
        requestId: (data && data.request_id) || '',
        payload: data,
      });
    }
    return data;
  }

  // ---- 健康 / 能力 -----------------------------------------------------
  health() {
    if (this.local) return Promise.resolve({ ok: true, version: '1.0-local', mode: 'local' });
    return this.call('GET', '/health', undefined, { timeout: 6000 });
  }
  capabilities() {
    if (this.local) return this.local.capabilities();
    return this.call('GET', '/capabilities');
  }

  // ---- 消息 -----------------------------------------------------------
  listMessages({ limit = 20, offset = 0 } = {}) {
    if (this.local) return this.local.listMessages({ limit, offset });
    return this.call('GET', `/v1/messages?limit=${limit}&offset=${offset}`);
  }
  getMessage(id) {
    if (this.local) return this.local.getMessage(id);
    return this.call('GET', '/messages/' + encodeURIComponent(id));
  }
  importMessages(payload) {
    if (this.local) return this.local.importMessages(payload);
    return this.call('POST', '/imports', payload);
  }
  patchMessage(id, patch) {
    if (this.local) return this.local.patchMessage(id, patch);
    return this.call('PATCH', '/messages/' + encodeURIComponent(id), patch);
  }
  deleteMessage(id) {
    if (this.local) return this.local.deleteMessage(id);
    return this.call('DELETE', '/messages/' + encodeURIComponent(id));
  }

  // ---- 覆盖范围 / 索引 / 事件 ------------------------------------------
  coverage() {
    if (this.local) return this.local.coverage();
    return this.call('GET', '/coverage');
  }
  rebuildIndex() {
    if (this.local) return this.local.rebuildIndex();
    return this.call('POST', '/index/rebuild', {});
  }
  events() {
    if (this.local) return this.local.events();
    return this.call('GET', '/events');
  }

  // ---- 查询与会话 ------------------------------------------------------
  query(payload) {
    if (this.local) return this.local.query(payload);
    return this.call('POST', '/query', payload, { timeout: 45000 });
  }
  getSession(id) {
    if (this.local) return this.local.getSession(id);
    return this.call('GET', '/sessions/' + encodeURIComponent(id));
  }

  // ---- 请求日志 --------------------------------------------------------
  getRequest(id) {
    if (this.local) {
      return Promise.resolve({ request_id: id, mode: 'local', note: '本地模式没有服务端请求日志' });
    }
    return this.call('GET', '/requests/' + encodeURIComponent(id));
  }

  /* ---------------------------------------------------------------------
   * 后加接口（成员 D 计划中 / 成员 A 任务清单要求）
   * 这些接口在当前后端上 **还不存在**，直接调用会 404。
   * 统一走 probe()：404 视为「尚未实现」返回 {available:false}，
   * 而不是抛异常 —— 这样界面显示的是「接口未就绪」而不是「系统故障」。
   * 一旦成员 D 上线这些接口，前端不需要再改代码，自动变成可用。
   * ------------------------------------------------------------------- */
  async probe(method, path, body) {
    if (this.local) {
      // 本地模式没有群订阅 / 通知推送这类服务端接口，按「未实现」处理，
      // 各页面会自动降级到基于本地消息的替代展示。
      return { available: false, reason: 'NOT_IMPLEMENTED' };
    }
    try {
      const data = await this.call(method, path, body);
      return { available: true, data };
    } catch (err) {
      if (err.status === 404) {
        return { available: false, reason: 'NOT_IMPLEMENTED', error: err };
      }
      throw err;
    }
  }

  me() { return this.probe('GET', '/me'); }
  groups() { return this.probe('GET', '/groups'); }
  subscriptions() { return this.probe('GET', '/subscriptions'); }
  subscribe(groupKeys) { return this.probe('POST', '/subscriptions', { group_keys: groupKeys }); }
  unsubscribe(groupKey) {
    return this.probe('DELETE', '/subscriptions/' + encodeURIComponent(groupKey));
  }
  notifications({ after = '', limit = 50 } = {}) {
    const q = new URLSearchParams();
    if (after) q.set('after', after);
    q.set('limit', String(limit));
    return this.probe('GET', '/notifications?' + q.toString());
  }
  ackNotifications(groupKey, cursor) {
    return this.probe('POST', '/notifications/ack', { group_key: groupKey, cursor });
  }
  syncStatus() { return this.probe('GET', '/sync/status'); }
}

/* -------------------------------------------------------------------------
 * 导入体构造：把表单值变成后端接受的 messages[]
 * 后端校验要点（不满足会逐条报 INVALID_INPUT）：
 *   - content 必填，<= 20000 字符
 *   - group_label 可省略（默认「未知来源」），<= 100 字符
 *   - sent_at 可空；日期或带时区 ISO；单独日期按 UTC 日界解释
 *   - metadata 只允许 event_key / title / fields / revision_kind /
 *     supersedes / source_confirmed
 *   - fields 的值必须**逐字**出现在 content 中（最多 20 项）
 *   - revision_kind = correction 时 supersedes 只能指向同 event_key 的已有消息
 * ---------------------------------------------------------------------- */
export function buildMessage({ content, groupLabel, sentAt, meta }) {
  const item = { content: content.trim() };
  if (groupLabel && groupLabel.trim()) item.group_label = groupLabel.trim();
  if (sentAt) item.sent_at = toIso(sentAt);

  const m = {};
  if (meta) {
    if (meta.eventKey && meta.eventKey.trim()) m.event_key = meta.eventKey.trim();
    if (meta.title && meta.title.trim()) m.title = meta.title.trim();
    if (meta.fields && Object.keys(meta.fields).length) m.fields = { ...meta.fields };
    if (meta.revisionKind) m.revision_kind = meta.revisionKind;
    if (meta.supersedes && meta.supersedes.length) m.supersedes = [...meta.supersedes];
    if (meta.sourceConfirmed) m.source_confirmed = true;
  }
  if (Object.keys(m).length) item.metadata = m;
  return item;
}

/** datetime-local / date 字符串 -> 带时区 ISO（本机时区） */
export function toIso(value) {
  if (!value) return null;
  if (/^\d{4}-\d{2}-\d{2}$/.test(value)) {
    const d = new Date(value + 'T00:00:00');
    return Number.isNaN(d.getTime()) ? null : d.toISOString();
  }
  const d = new Date(value);
  return Number.isNaN(d.getTime()) ? null : d.toISOString();
}

/** 前端预检：字段值是否逐字出现在原文里 */
export function checkFields(content, fields) {
  const bad = [];
  Object.entries(fields || {}).forEach(([k, v]) => {
    if (!v) return;
    if (!content.includes(v)) bad.push({ name: k, value: v });
  });
  return bad;
}
