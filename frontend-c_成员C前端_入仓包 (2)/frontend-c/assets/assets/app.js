/* =========================================================================
 * app.js —— 应用外壳：路由、连接配置、全局状态
 * ========================================================================= */
import { Api } from './api.js';
import { LocalEngine } from './local-engine.js';
import { esc, toast, dialog, downloadJson } from './util.js';
import { openImportDialog } from './message-actions.js';
import * as overview from './views/overview.js';
import * as library from './views/library.js';
import * as search from './views/search.js';
import * as detail from './views/detail.js';
import * as groups from './views/groups.js';
import * as notifications from './views/notifications.js';
import * as integration from './views/integration.js';
import * as system from './views/system.js';

const SESSION_KEY = 'qunyi.sessions';

const api = new Api();

const state = {
  /** 'backend' 已连真实后端 | 'local' 用户明确选择的本地试用 | 'none' 未连接 */
  conn: 'none',
  capabilities: null,
  coverage: null,
  messagesTotal: 0,
  sessions: loadSessions(),
  currentSession: null,
  pendingEventKey: null,
};

/* ---------------------------------------------------------------- 路由 */
const TITLES = {
  overview: ['工作台', '我的群 · 最新通知 · 问一问'],
  library: ['消息库', '导入、修订与原文管理'],
  search: ['智能查找', '多轮追问 · 条件更正 · 原文溯源'],
  groups: ['我的群', '可访问的群与关注关系（保存在后端）'],
  notifications: ['通知中心', '按游标增量拉取 · 去重 · 已读'],
  integration: ['联调检查', '逐个接口记录真实响应 · 区分 404/403/超时'],
  system: ['系统状态', 'AI 实际调用状态、存储与自检'],
  detail: ['原文详情', '单条原文与所属事件版本'],
};

function parseHash() {
  const raw = location.hash.replace(/^#\/?/, '') || 'overview';
  const [pathPart, queryPart] = raw.split('?');
  const parts = pathPart.split('/').filter(Boolean);
  const params = {};
  new URLSearchParams(queryPart || '').forEach((v, k) => (params[k] = v));
  return { name: parts[0] || 'overview', parts, params };
}

function navigate(hash) {
  if (location.hash === hash) renderRoute();
  else location.hash = hash;
}

function openDetail(messageId) {
  navigate('#/detail/' + encodeURIComponent(messageId));
}

let renderToken = 0;
async function renderRoute() {
  const { name, parts, params } = parseHash();
  const view = document.getElementById('view');
  const title = TITLES[name] || TITLES.overview;
  document.getElementById('page-title').textContent = title[0];
  document.getElementById('page-sub').textContent = title[1];
  document.title = `群忆 · ${title[0]}`;

  // 离开通知中心时停掉 30 秒轮询，避免后台空转
  if (name !== 'notifications' && typeof ctx.__disposeNotif === 'function') {
    ctx.__disposeNotif();
    ctx.__disposeNotif = null;
  }

  document.querySelectorAll('#nav .nav__item').forEach((btn) => {
    const route = btn.dataset.route.replace('#/', '');
    btn.classList.toggle('is-active', route === name);
    if (route === name) btn.setAttribute('aria-current', 'page');
    else btn.removeAttribute('aria-current');
  });
  closeNav();

  const token = ++renderToken;
  try {
    if (name === 'detail') {
      if (!parts[1]) return navigate('#/library');
      await detail.render(view, ctx, decodeURIComponent(parts[1]));
    } else if (name === 'library') {
      await library.render(view, ctx);
    } else if (name === 'search') {
      await search.render(view, ctx);
    } else if (name === 'groups') {
      await groups.render(view, ctx);
    } else if (name === 'notifications') {
      await notifications.render(view, ctx);
    } else if (name === 'integration') {
      await integration.render(view, ctx);
    } else if (name === 'system') {
      await system.render(view, ctx, params);
    } else {
      await overview.render(view, ctx);
    }
  } catch (err) {
    if (token !== renderToken) return;
    view.innerHTML = `<div class="notice notice--danger"><span class="notice__icon">✕</span>
      <div><strong>${esc(err.code || 'ERROR')}</strong>：${esc(err.message || String(err))}
      ${err.hint ? `<div class="small" style="margin-top:4px">${esc(err.hint)}</div>` : ''}</div></div>`;
    if (err.status === 401) ctx.handleError(err);
  }
  if (token === renderToken) {
    window.scrollTo(0, 0);
    view.focus({ preventScroll: true });
  }
}

/* ---------------------------------------------------------- 会话本地记录 */
function loadSessions() {
  try {
    const raw = localStorage.getItem(SESSION_KEY);
    return raw ? JSON.parse(raw) : [];
  } catch {
    return [];
  }
}
function saveSessions() {
  try {
    localStorage.setItem(SESSION_KEY, JSON.stringify(state.sessions.slice(0, 12)));
  } catch {
    /* 忽略配额错误 */
  }
}
function rememberSession({ id, title, status }) {
  if (!id) return;
  const exists = state.sessions.find((s) => s.id === id);
  if (exists) {
    Object.assign(exists, { title: title || exists.title, status, updatedAt: new Date().toISOString(), invalid: false });
  } else {
    state.sessions.unshift({ id, title, status, updatedAt: new Date().toISOString() });
  }
  state.sessions = state.sessions.slice(0, 12);
  saveSessions();
}
function markSessionInvalid(id) {
  const s = state.sessions.find((x) => x.id === id);
  if (s) s.invalid = true;
  saveSessions();
}
function clearSessions(reason) {
  state.sessions = [];
  state.currentSession = null;
  saveSessions();
  if (reason) toast(reason, 'warn');
}

function exportSession(s) {
  const payload = {
    session_id: s.id,
    conditions: s.conditions,
    exported_at: new Date().toISOString(),
    turns: s.turns,
  };
  downloadJson(`qunyi-session-${(s.id || 'draft').slice(0, 8)}.json`, payload);
}

/* ----------------------------------------------------------------- 外壳 */
function refreshShell() {
  const dot = document.getElementById('conn-dot');
  const text = document.getElementById('conn-text');
  const mode = document.getElementById('conn-mode');
  const badge = document.getElementById('mode-badge');
  const foot = document.getElementById('conn-foot');
  const cap = state.capabilities;
  const offline = typeof navigator !== 'undefined' && navigator.onLine === false;

  if (state.conn === 'backend') {
    dot.className = offline ? 'dot dot--warn' : 'dot dot--ok';
    text.textContent = offline ? '后端已连 · 当前断网' : '已连接后端';
    mode.textContent = cap
      ? `v${cap.version || '?'} · mode=${cap.mode || '?'}${cap.simulation ? ' · 模拟' : ''}${api.demoReadonly ? ' · 只读演示' : ''}`
      : '正在读取能力…';
    // 这里只表示「后端怎么配置的」；本次请求是否真的调用了模型，看系统状态页
    badge.textContent = api.demoReadonly
      ? '只读演示'
      : cap ? (cap.simulation || cap.mode === 'mock' ? '模拟模式' : 'live 已配置') : '—';
    badge.className = 'badge ' + (api.demoReadonly || (cap && (cap.simulation || cap.mode === 'mock')) ? 'badge--warn' : 'badge--ok');
    if (foot) foot.innerHTML = api.demoReadonly
      ? '演示环境：只可浏览与查询<br />导入 / 修订 / 删除已停用'
      : '数据来自后端 · 令牌只存本机<br />可随时退出登录清除';
  } else if (state.conn === 'local') {
    const n = state.coverage ? state.coverage.imported_count : 0;
    dot.className = 'dot dot--warn';
    text.textContent = '本地试用 · 未连后端';
    mode.textContent = `浏览器存储 · ${n} 条 · 非真实数据`;
    badge.textContent = '本地试用';
    badge.className = 'badge badge--warn';
    if (foot) foot.innerHTML = '本地试用：数据只在这台设备<br />不等于真实 QQ 消息';
  } else {
    dot.className = 'dot dot--warn';
    text.textContent = '未连接后端';
    mode.textContent = '点下方登录';
    badge.textContent = '未连接';
    badge.className = 'badge badge--warn';
    if (foot) foot.innerHTML = '未登录 · 无数据';
  }

  const count = document.getElementById('nav-msg-count');
  if (count) count.textContent = state.coverage ? String(state.coverage.imported_count) : state.messagesTotal ? String(state.messagesTotal) : '';
  const logout = document.getElementById('btn-logout');
  if (logout) logout.style.display = state.conn === 'none' ? 'none' : '';
}

/* --------------------------------------------------------- 登录 / 连接 */
let connectDialogRef = null;

const LOGIN_NOTE = `正式登录接口（用户账号、群成员权限）由后端提供，前端按约定接入。
在它上线前，这里用<strong>工作区令牌</strong>做过渡鉴权——它只代表"能访问哪个数据空间"，
<strong>不是</strong>用户登录，也不代表拥有群成员权限。`;

function openConnectDialog(intro) {
  if (connectDialogRef) {
    connectDialogRef.close();
    connectDialogRef = null;
  }
  const dlg = dialog({
    title: '登录 · 连接后端',
    width: 620,
    body: `
      ${intro ? `<div class="notice notice--warn" style="margin-bottom:14px"><span class="notice__icon">!</span><div>${esc(intro)}</div></div>` : ''}
      <div class="notice notice--info" style="margin-bottom:14px"><span class="notice__icon">i</span>
        <div>${LOGIN_NOTE}</div></div>
      <div class="stack">
        <div class="field">
          <label>后端地址</label>
          <input class="input mono" id="cfg-base" value="${esc(api.base)}" placeholder="/gw 或 https://你的后端域名" />
          <span class="hint">同源部署保持 <code>/gw</code>（由代理转发到后端）。
            直填 <code>http://127.0.0.1:8000</code> 需要后端允许跨域，否则浏览器会拦下。</span>
        </div>
        <div class="field">
          <label>访问令牌</label>
          <input class="input mono" id="cfg-token" type="password" value="${esc(api.token)}" placeholder="粘贴 state/credentials.json 里的 key" />
          <span class="hint">令牌只保存在本机；不勾选"记住"时关掉标签页即失效。
            不要把令牌写进 URL、截图或仓库。</span>
        </div>
        <label class="checkbox"><input type="checkbox" id="cfg-remember" ${localStorage.getItem('qunyi.keepToken') === '1' ? 'checked' : ''} />
          在这台设备上记住令牌（写入 localStorage）</label>
        <div id="cfg-result"></div>
      </div>`,
    footer: `<button class="btn btn--ghost" id="cfg-trial">先用本地试用</button>
             <span class="spacer"></span>
             <button class="btn btn--ghost" data-close="1">取消</button>
             <button class="btn btn--primary" id="cfg-test">登录并连接</button>`,
  });
  connectDialogRef = dlg;
  dlg.onClose(() => { connectDialogRef = null; });

  const baseEl = dlg.panel.querySelector('#cfg-base');
  const tokenEl = dlg.panel.querySelector('#cfg-token');
  const rememberEl = dlg.panel.querySelector('#cfg-remember');
  const out = dlg.panel.querySelector('#cfg-result');

  dlg.panel.querySelector('#cfg-trial').onclick = () => {
    dlg.close();
    enterLocalTrial();
  };

  dlg.panel.querySelector('#cfg-test').onclick = async (e) => {
    const btn = e.currentTarget;
    btn.disabled = true;
    btn.textContent = '连接中…';
    out.innerHTML = '';
    try {
      const { cap } = await tryConnect({
        base: baseEl.value.trim(),
        token: tokenEl.value.trim(),
        remember: rememberEl.checked,
      });
      out.innerHTML = `<div class="notice notice--info"><span class="notice__icon">✓</span>
        <div>已连接：后端 v${esc(cap.version || '?')}，mode=<code>${esc(cap.mode || '?')}</code>${
          cap.simulation ? '，当前是模拟适配器' : ''
        }。</div></div>`;
      state.conn = 'backend';
      state.capabilities = cap;
      refreshShell();
      toast('已连接后端', 'ok');
      setTimeout(async () => {
        dlg.close();
        try {
          await bootstrapData();
        } catch { /* 各页面自己提示 */ }
        renderRoute();
      }, 400);
    } catch (err) {
      state.conn = 'none';
      api.local = null;
      out.innerHTML = errorNotice(err, '连接失败');
      refreshShell();
    } finally {
      btn.disabled = false;
      btn.textContent = '登录并连接';
    }
  };
}

/** 统一的错误提示块：把 HTTP 状态、后端错误码、request_id 都摆出来 */
function errorNotice(err, title) {
  const e = err || {};
  const rid = e.requestId ? `<div class="small mono" style="margin-top:4px">request_id: ${esc(e.requestId)}</div>` : '';
  return `<div class="notice notice--danger"><span class="notice__icon">✕</span>
    <div><strong>${esc(title || '请求失败')}</strong>：<code>${esc(e.code || 'ERROR')}</code> ${esc(e.message || '')}
      ${e.status ? `<span class="small muted">（HTTP ${esc(String(e.status))}）</span>` : ''}
      ${e.hint ? `<div class="small" style="margin-top:4px">${esc(e.hint)}</div>` : ''}
      ${rid}
    </div></div>`;
}

/**
 * 真连一次后端：health（公开）+ capabilities（需鉴权）。
 * 没填令牌时先试一次 capabilities —— 服务端注入令牌的部署（代理注入）能直接通过。
 */
async function tryConnect({ base, token, remember }) {
  api.saveConfig({ base, token, remember });
  api.local = null;
  api.serverAuth = false;
  if (!token) {
    await api.capabilities(); // 失败就直接抛给调用方
    api.serverAuth = true;
  }
  const health = await api.health();
  const cap = await api.capabilities();
  return { health, cap };
}

/* ------------------------------------------------------------ 登录闸门 */
function renderGate(err) {
  state.conn = 'none';
  api.local = null;
  state.capabilities = null;
  refreshShell();
  const view = document.getElementById('view');
  document.getElementById('page-title').textContent = '登录';
  document.getElementById('page-sub').textContent = '连接后端后，页面显示的是真实数据';
  document.title = '群忆 · 登录';

  view.innerHTML = `
    <div class="gate">
      <div class="gate__card">
        <h2 style="margin:0 0 6px">登录 · 群忆</h2>
        <p class="small muted" style="margin:0 0 16px">${LOGIN_NOTE}</p>
        ${err ? (err.status === 404
          ? '<div class="notice notice--info"><span class="notice__icon">i</span><div>这是<strong>纯静态部署</strong>，本站没有后端。点下方「不连后端，先本地试用」即可开始使用；「后端地址 / 令牌」只在你自己电脑上跑过后端时才需要填。</div></div>'
          : errorNotice(err, '后端连接失败')) : ''}
        <div class="stack" style="margin-top:14px">
          <div class="field">
            <label>后端地址</label>
            <input class="input mono" id="gate-base" value="${esc(api.base)}" placeholder="/gw" />
          </div>
          <div class="field">
            <label>访问令牌</label>
            <input class="input mono" id="gate-token" type="password" placeholder="粘贴 state/credentials.json 里的 key" />
          </div>
          <label class="checkbox"><input type="checkbox" id="gate-remember" /> 在这台设备上记住令牌</label>
          <div id="gate-result"></div>
          <button class="btn btn--primary btn--block" id="gate-submit">登录并连接</button>
          <button class="btn btn--ghost btn--block" id="gate-trial">不连后端，先本地试用</button>
          <div class="small muted">本地试用只在浏览器里存数据、用词面检索，<strong>不是</strong>真实 QQ 消息，
            也不能代表项目已完成联调。</div>
        </div>
      </div>
    </div>`;

  const out = view.querySelector('#gate-result');
  view.querySelector('#gate-submit').onclick = async (e) => {
    const btn = e.currentTarget;
    btn.disabled = true;
    btn.textContent = '连接中…';
    try {
      const { cap } = await tryConnect({
        base: view.querySelector('#gate-base').value.trim(),
        token: view.querySelector('#gate-token').value.trim(),
        remember: view.querySelector('#gate-remember').checked,
      });
      state.conn = 'backend';
      state.capabilities = cap;
      toast('已连接后端', 'ok');
      await bootstrapData().catch(() => {});
      location.hash = '#/overview';
      renderRoute();
    } catch (ex) {
      out.innerHTML = errorNotice(ex, '连接失败');
      state.conn = 'none';
      refreshShell();
    } finally {
      btn.disabled = false;
      btn.textContent = '登录并连接';
    }
  };
  view.querySelector('#gate-trial').onclick = () => enterLocalTrial();
}

/* ------------------------------------------------------------ 本地试用 */
function enterLocalTrial() {
  api.clearToken();
  api.serverAuth = false;
  api.local = new LocalEngine();
  state.conn = 'local';
  state.capabilities = null;
  sessionStorage.setItem('qunyi.trial', '1');
  refreshShell();
  bootstrapData().catch(() => {});
  location.hash = '#/overview';
  renderRoute();
  toast('已进入本地试用：数据只存在这台设备，不是真实群消息', 'warn', 6000);
}

/* -------------------------------------------------------------- 退出登录 */
function logout() {
  api.clearToken();
  api.local = null;
  api.serverAuth = false;
  state.conn = 'none';
  state.capabilities = null;
  state.coverage = null;
  state.currentSession = null;
  // 文档要求：退出后清掉敏感的本地缓存，只保留最少必要数据
  state.sessions = [];
  try {
    localStorage.removeItem('qunyi.sessions'); // 直接删键，而不是留一个空数组
  } catch {
    /* 忽略 */
  }
  ['qunyi.notif.seen', 'qunyi.notif.cursor', 'qunyi.notif.localAck', 'qunyi.notif.syncedAt', 'qunyi.lastQuery', 'qunyi.trial']
    .forEach((k) => {
      localStorage.removeItem(k);
      sessionStorage.removeItem(k);
    });
  if (typeof ctx.__disposeNotif === 'function') {
    ctx.__disposeNotif();
    ctx.__disposeNotif = null;
  }
  renderGate();
  toast('已退出登录，本地缓存已清除', 'ok');
}

/* ------------------------------------------------------------------ ctx */
const ctx = {
  api,
  state,
  navigate,
  toast,
  openDetail,
  refreshShell,
  rememberSession,
  markSessionInvalid,
  clearSessions,
  exportSession,
  openImport: (onDone) => openImportDialog({ api, onDone }),
  handleError(err) {
    if (!err) return;
    if (err.status === 401) {
      // 会话失效：清掉令牌，回到登录页，而不是继续在空页面上报错
      api.clearToken();
      state.conn = 'none';
      renderGate({ ...err, message: '登录已失效（401），请重新登录' });
    }
  },
};

/* --------------------------------------------------------------- 启动 */
async function bootstrapData() {
  try {
    const cap = await api.capabilities();
    state.capabilities = cap;
    const cov = await api.coverage();
    state.coverage = cov;
  } catch (err) {
    if (err.status === 401) throw err;
    /* 其他错误先不阻断页面渲染，各页面自己会提示 */
  } finally {
    refreshShell();
  }
}

/* ------------------------------------------------------- 移动端抽屉导航 */
function openNav() {
  document.body.classList.add('nav-open');
  const btn = document.getElementById('btn-nav-open');
  if (btn) btn.setAttribute('aria-expanded', 'true');
  const first = document.querySelector('#nav .nav__item');
  if (first && window.matchMedia('(max-width: 900px)').matches) first.focus();
}
function closeNav() {
  if (!document.body.classList.contains('nav-open')) return;
  document.body.classList.remove('nav-open');
  const btn = document.getElementById('btn-nav-open');
  if (btn) {
    btn.setAttribute('aria-expanded', 'false');
    btn.focus();
  }
}

function bindShell() {
  document.querySelectorAll('#nav .nav__item').forEach((btn) => {
    btn.onclick = () => navigate(btn.dataset.route);
  });
  const openBtn = document.getElementById('btn-nav-open');
  if (openBtn) openBtn.onclick = () => (document.body.classList.contains('nav-open') ? closeNav() : openNav());
  const closeBtn = document.getElementById('btn-nav-close');
  if (closeBtn) closeBtn.onclick = closeNav;
  const scrim = document.getElementById('nav-scrim');
  if (scrim) scrim.onclick = closeNav;
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape' && document.body.classList.contains('nav-open')) closeNav();
  });
  document.getElementById('btn-connect').onclick = () =>
    state.conn === 'none' ? renderGate() : openConnectDialog();
  const logoutBtn = document.getElementById('btn-logout');
  if (logoutBtn) logoutBtn.onclick = () => logout();
  // 断网 / 恢复：如实告诉用户当前状态；恢复后自动补拉，避免"以为数据没更新"
  window.addEventListener('offline', () => {
    refreshShell();
    toast(
      state.conn === 'backend' ? '已断网：页面显示的可能是旧数据，恢复后会自动补拉' : '已断网，本地试用数据仍然可用',
      'warn',
    );
  });
  window.addEventListener('online', async () => {
    refreshShell();
    if (state.conn === 'backend') {
      try {
        await bootstrapData();
        await renderRoute();
        toast('网络已恢复，已重新拉取', 'ok');
      } catch (err) {
        toast(`恢复后拉取失败：${err.code}`, 'error');
      }
    } else {
      toast('网络已恢复', 'ok');
    }
  });
  document.getElementById('btn-refresh').onclick = async () => {
    try {
      await bootstrapData();
      await renderRoute();
      toast('已刷新覆盖范围', 'ok');
    } catch (err) {
      toast(`${err.code}：${err.message}`, 'error');
      ctx.handleError(err);
    }
  };
  document.getElementById('btn-quick-ask').onclick = () => {
    navigate('#/search');
    setTimeout(() => {
      const el = document.getElementById('ask');
      if (el) el.focus();
    }, 120);
  };
  window.addEventListener('hashchange', renderRoute);
}

async function boot() {
  bindShell();

  /* 安全要求：不再从 URL 读取令牌（地址栏、访问日志、截图里都不该出现）。
     老的 ?token= 链接在这里被就地清掉，不再采用。 */
  const sp = new URLSearchParams(location.search);
  if (sp.has('token')) {
    sp.delete('token');
    const qs = sp.toString();
    history.replaceState(null, '', location.pathname + (qs ? '?' + qs : '') + location.hash);
  }

  /* 用户上一次明确选了"本地试用"：尊重这个选择，但侧栏会一直标明不是真实数据。 */
  if (sessionStorage.getItem('qunyi.trial') === '1') {
    enterLocalTrial();
    return;
  }

  /* 默认走真实后端：先探 capabilities（没令牌时也试一次，代理可能已在服务端注入令牌）。 */
  try {
    if (!api.token) {
      await api.call('GET', '/capabilities', undefined, { timeout: 5000 });
      api.serverAuth = true;
    }
    const cap = await api.capabilities();
    state.conn = 'backend';
    state.capabilities = cap;
    api.local = null;
    refreshShell();
    await bootstrapData().catch(() => {});
    renderRoute();
  } catch (err) {
    if (err.status === 401 && api.token) {
      api.clearToken();
      renderGate({ ...err, message: '令牌无效或已过期，请重新登录' });
    } else {
      renderGate(err.status ? err : null);
    }
  }
}

boot();
