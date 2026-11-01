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
import * as system from './views/system.js';

const SESSION_KEY = 'qunyi.sessions';

const api = new Api();

const state = {
  capabilities: null,
  coverage: null,
  messagesTotal: 0,
  sessions: loadSessions(),
  currentSession: null,
  pendingEventKey: null,
};

/* ---------------------------------------------------------------- 路由 */
const TITLES = {
  overview: ['工作台', '粘贴群通知 · 提问 · 直接拿答案'],
  library: ['消息库', '导入、修订与原文管理'],
  search: ['智能查找', '多轮追问 · 条件更正 · 原文溯源'],
  groups: ['群管理', '可访问的群与关注关系'],
  notifications: ['通知中心', '新消息增量提醒 · 去重 · 已读'],
  system: ['系统状态', '运行模式、存储占用与自检'],
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
    if (err.status === 401) openConnectDialog('令牌无效或已更换，请重新填写。');
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

  if (api.isLocal) {
    const n = state.coverage ? state.coverage.imported_count : 0;
    const offline = typeof navigator !== 'undefined' && navigator.onLine === false;
    dot.className = offline ? 'dot dot--warn' : 'dot dot--ok';
    text.textContent = offline ? '离线 · 本地仍可用' : '本地空间运行中';
    mode.textContent = `浏览器存储 · 已存 ${n} 条`;
    badge.textContent = offline ? '离线' : '本地引擎';
    badge.className = offline ? 'badge badge--warn' : 'badge badge--ok';
  } else if (!api.configured) {
    const cap = state.capabilities;
    dot.className = 'dot dot--ok';
    text.textContent = '已连接后端';
    mode.textContent = `mode=${cap.mode}${cap.simulation ? ' · 模拟' : ''} · 检索 ${String(cap.search || '').split('_')[0]}`;
    badge.textContent = cap.simulation ? '模拟模式' : '真实模型';
    badge.className = 'badge ' + (cap.simulation ? 'badge--warn' : 'badge--ok');
  } else {
    dot.className = 'dot dot--warn';
    text.textContent = '令牌已填，未验证';
    mode.textContent = '—';
    badge.textContent = '待验证';
    badge.className = 'badge';
  }

  const count = document.getElementById('nav-msg-count');
  if (count) count.textContent = state.coverage ? String(state.coverage.imported_count) : state.messagesTotal ? String(state.messagesTotal) : '';
}

/* ------------------------------------------------------------- 连接弹层 */
let connectDialogRef = null;
function openConnectDialog(intro) {
  if (connectDialogRef) {
    connectDialogRef.close();
    connectDialogRef = null;
  }
  const dlg = dialog({
    title: '连接成员 D 后端',
    width: 600,
    body: `
      ${intro ? `<div class="notice notice--warn" style="margin-bottom:14px"><span class="notice__icon">!</span><div>${esc(intro)}</div></div>` : ''}
      <div class="stack">
        <div class="field">
          <label>API 地址</label>
          <input class="input mono" id="cfg-base" value="${esc(api.base)}" />
          <span class="hint">保持 <code>/gw</code> 即可（由 serve.py 同源转发到后端；<code>/api</code> 仍兼容）。
            直接填 <code>http://127.0.0.1:8000</code> 会因为后端没有 CORS 头而被浏览器拦下。</span>
        </div>
        <div class="field">
          <label>访问令牌</label>
          <input class="input mono" id="cfg-token" type="password" value="${esc(api.token)}" placeholder="粘贴 state/credentials.json 里的 key" />
          <span class="hint">后端首次启动会生成随机令牌，文件位于 <code>member-d/backend/state/credentials.json</code>。
            令牌只保存在你这台机器上，不要提交到仓库或截图外发。</span>
        </div>
        <label class="checkbox"><input type="checkbox" id="cfg-remember" ${localStorage.getItem('qunyi.keepToken') === '1' ? 'checked' : ''} />
          在这台电脑记住令牌（写入 localStorage，关闭浏览器仍保留）</label>
        <div id="cfg-result"></div>
      </div>`,
    footer: `<button class="btn btn--ghost" data-close="1">取消</button>
             <button class="btn" id="cfg-test">测试并保存</button>`,
  });
  connectDialogRef = dlg;
  dlg.onClose(() => { connectDialogRef = null; });

  const baseEl = dlg.panel.querySelector('#cfg-base');
  const tokenEl = dlg.panel.querySelector('#cfg-token');
  const rememberEl = dlg.panel.querySelector('#cfg-remember');
  const out = dlg.panel.querySelector('#cfg-result');

  dlg.panel.querySelector('#cfg-test').onclick = async (e) => {
    const btn = e.currentTarget;
    btn.disabled = true;
    btn.textContent = '测试中…';
    api.saveConfig({ base: baseEl.value.trim(), token: tokenEl.value.trim(), remember: rememberEl.checked });
    api.local = null; // 先离开本地模式，真正去打后端
    out.innerHTML = '';
    try {
      const health = await api.health();
      const cap = await api.capabilities();
      state.capabilities = cap;
      out.innerHTML = `<div class="notice notice--info"><span class="notice__icon">✓</span>
        <div>连接成功：后端 v${esc(health.version)}，模式 <code>${esc(cap.mode)}</code>，
        ${cap.simulation ? '当前是模拟适配器（simulation=true）' : '已接真实模型'}。</div></div>`;
      refreshShell();
      toast('已连接后端', 'ok');
      setTimeout(() => {
        dlg.close();
        bootstrapData().then(renderRoute);
      }, 500);
    } catch (err) {
      api.local = new LocalEngine(); // 连不上就退回本地，界面不空转
      out.innerHTML = `<div class="notice notice--danger"><span class="notice__icon">✕</span>
        <div><strong>${esc(err.code)}</strong>：${esc(err.message)}
        <div class="small" style="margin-top:4px">已保持本地模式，数据与检索照常可用。</div></div></div>`;
      refreshShell();
    } finally {
      btn.disabled = false;
      btn.textContent = '测试并保存';
    }
  };
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
      openConnectDialog('后端返回 401：令牌缺失或无效。');
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
  document.getElementById('btn-connect').onclick = () => openConnectDialog();
  // 断网 / 恢复：本地模式不受影响，但要如实告诉用户当前状态
  window.addEventListener('offline', () => {
    refreshShell();
    toast('已断网，本地数据与检索仍然可用', 'warn');
  });
  window.addEventListener('online', () => {
    refreshShell();
    toast('网络已恢复', 'ok');
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

  /* 默认跑本地引擎：打开就能导入、提问、导出，不需要后端，也不需要令牌。
     后端只有在你明确要求时才启用（地址加 ?backend=1，或在连接设置里填令牌）。 */
  api.local = new LocalEngine();

  const sp = new URLSearchParams(location.search);

  /* 兼容老的预置链接：?token=xxx 时尝试切到后端（不落地存储，仅当前会话）。
     清理时保留 sharecode（公网网关要求每个请求都能从地址取到它）。 */
  const urlToken = sp.get('token');
  if (urlToken && !api.token) {
    api.saveConfig({ base: api.base, token: urlToken, remember: false });
    const keep = new URLSearchParams();
    if (sp.get('sharecode')) keep.set('sharecode', sp.get('sharecode'));
    const qs = keep.toString();
    history.replaceState(null, '', location.pathname + (qs ? '?' + qs : '') + location.hash);
  }

  if (sp.has('backend') || api.token) {
    try {
      if (!api.token) {
        await api.call('GET', '/capabilities', undefined, { timeout: 4000 });
        api.serverAuth = true;
      }
      const cap = await api.capabilities();
      api.local = null;
      state.capabilities = cap;
    } catch {
      /* 连不上就老老实实用本地引擎，页面照常工作 */
      api.serverAuth = false;
    }
  }

  refreshShell();

  try {
    await bootstrapData();
  } catch (err) {
    if (err.status === 401) toast('后端令牌无效，已改用本地引擎', 'warn');
  }
  renderRoute();
}

boot();
