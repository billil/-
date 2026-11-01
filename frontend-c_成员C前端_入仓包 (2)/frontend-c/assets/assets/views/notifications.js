/* 通知中心：游标增量 + 分页 + 去重 + 已读
 *
 * 主线（后端就绪时）：GET /v1/notifications?after=<cursor>&limit=50
 *   - 按 next_cursor 逐页拉到 has_more=false，最多 10 页，避免死循环
 *   - 按 message_id 去重（本次 + localStorage 历史），重复刷新不叠加
 *   - 页面隐藏时停表；重新可见 / 网络恢复 / 重新进入时立即补拉
 *   - 明确显示：最近同步时间、本次新增、加载中、无新消息、断线、权限失效
 *
 * 降级（后端 404 时）：用 GET /v1/messages 的增量轮询做等价视图，
 *   并在页头写明"这不是服务端通知接口，已读只在本机"。
 */
import { esc, fmtTime, relTime, toast, truncate } from '../util.js';
import { openMessageDrawer } from '../message-actions.js';

const LS_SEEN = 'qunyi.notif.seen';
const LS_CURSOR = 'qunyi.notif.cursor';
const LS_SYNCED = 'qunyi.notif.syncedAt';
const LS_LOCAL_ACK = 'qunyi.notif.localAck';

const ui = {
  timer: null,
  inflight: false,
  intervalMs: 30000,
  items: [],
  state: 'loading',
  lastError: null,
  pages: 0,
  newCount: 0,
};

/* ------------------------------------------------------------ 本地存储 */
function loadSeen() {
  try {
    return new Set(JSON.parse(localStorage.getItem(LS_SEEN) || '[]'));
  } catch {
    return new Set();
  }
}
function saveSeen(set) {
  try {
    localStorage.setItem(LS_SEEN, JSON.stringify(Array.from(set).slice(-1500)));
  } catch {
    /* 去重是增强，存不下不影响主流程 */
  }
}
const loadCursor = () => localStorage.getItem(LS_CURSOR) || '';
const saveCursor = (c) => c && localStorage.setItem(LS_CURSOR, String(c));
const loadLocalAck = () => localStorage.getItem(LS_LOCAL_ACK) || '';

/* ---------------------------------------------------------------- 渲染 */
export async function render(root, ctx) {
  dispose(); // 进页面先清掉上一轮，避免重复计时器
  ui.items = [];
  ui.state = 'loading';

  root.innerHTML = `
    <div class="page-head">
      <div>
        <h2>通知中心</h2>
        <p>新消息进来时在这里增量出现。同一条消息<strong>只出现一次</strong>；
          页面关闭期间由后端持续接收，重新打开时自动补拉。</p>
      </div>
      <span class="spacer"></span>
      <button class="btn btn--sm" data-act="refresh">立即刷新</button>
    </div>
    <div id="notif-status"></div>
    <div id="notif-body"><div class="empty"><span class="empty__icon">◌</span>正在读取通知…</div></div>
  `;

  root.querySelector('[data-act="refresh"]').onclick = () => refresh(root, ctx, { manual: true });
  await refresh(root, ctx, { manual: false });

  ui.timer = setInterval(() => {
    if (document.visibilityState === 'visible') refresh(root, ctx, { manual: false });
  }, ui.intervalMs);

  // 页面重新可见立即补拉（切换标签页、锁屏回来）
  document.addEventListener('visibilitychange', onVisible);
  window.addEventListener('online', onOnline);
  ctx.__disposeNotif = dispose;
}

function onVisible() {
  if (document.visibilityState === 'visible' && ctxRef && rootRef) {
    refresh(rootRef, ctxRef, { manual: false });
  }
}
function onOnline() {
  if (ctxRef && rootRef) refresh(rootRef, ctxRef, { manual: false });
}

export function dispose() {
  if (ui.timer) {
    clearInterval(ui.timer);
    ui.timer = null;
  }
  document.removeEventListener('visibilitychange', onVisible);
  window.removeEventListener('online', onOnline);
  ctxRef = null;
  rootRef = null;
}

let ctxRef = null;
let rootRef = null;

/* ---------------------------------------------------------------- 刷新 */
async function refresh(root, ctx, { manual }) {
  if (!root.isConnected) return;
  ctxRef = ctx;
  rootRef = root;
  if (ui.inflight) return;
  ui.inflight = true;
  const body = root.querySelector('#notif-body');
  const status = root.querySelector('#notif-status');
  if (!body) {
    ui.inflight = false;
    return;
  }
  if (manual) body.classList.add('is-loading');

  try {
    if (typeof navigator !== 'undefined' && navigator.onLine === false) {
      ui.state = 'offline';
      renderStatus(status, ctx);
      if (!ui.items.length) body.innerHTML = offlineHtml();
      return;
    }

    const res = await ctx.api.notifications({ after: loadCursor(), limit: 50 });
    if (!res.available) {
      if (res.reason === 'NOT_IMPLEMENTED') {
        await renderFallback(body, status, root, ctx);
      } else {
        ui.state = 'error';
        ui.lastError = res.error;
        renderStatus(status, ctx);
        body.innerHTML = errorHtml(res.error);
      }
      return;
    }

    const { items, pages } = await pullAll(ctx, res.data);
    ui.pages = pages;
    ui.state = items.length ? 'ok' : 'empty';
    ui.items = items;
    localStorage.setItem(LS_SYNCED, new Date().toISOString());
    renderStatus(status, ctx);
    body.innerHTML = items.length ? listHtml(items, true) : emptyHtml(true);
    bindList(root, ctx, items, true);
    markNav(items);
  } catch (err) {
    ui.state = err.status === 403 ? 'forbidden' : err.status === 401 ? 'auth' : 'error';
    ui.lastError = err;
    renderStatus(status, ctx);
    body.innerHTML = errorHtml(err);
    if (err.status === 401 || err.status === 403) ctx.handleError(err);
  } finally {
    ui.inflight = false;
    body.classList.remove('is-loading');
  }
}

/**
 * 逐页拉取：while has_more && next_cursor 变化。
 * 后端未给 has_more 时，用「是否拿满一页 + cursor 是否变化」推断。
 */
async function pullAll(ctx, first) {
  const seen = loadSeen();
  const byId = new Map();
  const fresh = new Set();
  let data = first;
  let pages = 0;
  let after = loadCursor();

  while (pages < 10) {
    pages += 1;
    const list = normalizeNotifs(data);
    list.forEach((n) => {
      if (!n.id) return;
      byId.set(n.id, n);
      if (!seen.has(n.id)) fresh.add(n.id);
    });

    const prev = after;
    const next = data.next_cursor || data.cursor || '';
    const more =
      data.has_more !== undefined ? Boolean(data.has_more) : Boolean(next && next !== prev && list.length >= 50);
    if (next && next !== prev) {
      saveCursor(next);
      after = next;
    }
    // 没有下一页 / 游标没有前进 / 已到页数上限 → 停
    if (!more || !next || next === prev) break;

    // 继续拉下一页
    const r = await ctx.api.notifications({ after: next, limit: 50 });
    if (!r.available) break;
    data = r.data;
  }

  const ids = Array.from(byId.keys());
  ids.forEach((id) => seen.add(id));
  saveSeen(seen);

  ui.newCount = fresh.size;
  const items = ids.map((id) => byId.get(id)).sort((a, b) => (b.importedAt || '').localeCompare(a.importedAt || ''));
  return { items, pages };
}

/* ------------------------------- 降级：后端未实现通知接口时的等价视图 */
async function renderFallback(body, status, root, ctx) {
  const page = await ctx.api.listMessages({ limit: 100, offset: 0 });
  const all = page.items || [];
  const seen = loadSeen();
  const localAck = loadLocalAck();
  const firstRun = seen.size === 0 && !localAck;
  const fresh = firstRun ? [] : all.filter((m) => !seen.has(m.id));
  all.forEach((m) => seen.add(m.id));
  saveSeen(seen);

  ui.state = 'fallback';
  ui.items = [];
  localStorage.setItem(LS_SYNCED, new Date().toISOString());
  renderStatus(status, ctx, { fallback: true, total: all.length, fresh: fresh.length, firstRun });

  const items = (fresh.length ? fresh : all.map((m) => ({ ...m, _old: true }))).map((m) => ({
    id: m.id,
    groupKey: m.group_label,
    group: m.group_label || '未知来源',
    content: m.content || '',
    sentAt: m.sent_at,
    importedAt: m.imported_at,
    kind: (m.metadata && m.metadata.revision_kind) || 'initial',
    eventKey: (m.metadata && m.metadata.event_key) || '',
    unread: !m._old && (!localAck || (m.imported_at || '') > localAck),
  }));

  body.innerHTML = items.length ? listHtml(items, false) : emptyHtml(false);
  bindList(root, ctx, items, false);

  const ackAll = status.querySelector('[data-act="ackAll"]');
  if (ackAll) {
    ackAll.onclick = () => {
      const latest = all.reduce((acc, m) => ((m.imported_at || '') > acc ? m.imported_at : acc), '');
      localStorage.setItem(LS_LOCAL_ACK, latest || new Date().toISOString());
      toast('已把当前通知标记为已读（仅本机，不同步到其他设备）', 'ok');
      refresh(root, ctx, { manual: false });
    };
  }
}

/* ------------------------------------------------------------ 状态条 */
function renderStatus(host, ctx, extra = {}) {
  if (!host) return;
  const synced = localStorage.getItem(LS_SYNCED);
  const chips = [];

  if (extra.fallback) {
    chips.push('<span class="badge badge--warn">降级视图</span>');
    host.innerHTML = `<div class="notice notice--info" style="margin-bottom:12px"><span class="notice__icon">i</span>
      <div>
        后端尚未提供 <code>/v1/notifications</code>（404）。下面的列表由 <code>GET /v1/messages</code>
        增量轮询得到，每 30 秒一次；已读状态<strong>只在本机</strong>，不同步到其他设备。
        <div class="small muted" style="margin-top:4px">接口上线后本页自动切换为真正的通知接口。</div>
      </div></div>
      <div class="notif-toolbar">
        ${chips.join('')}
        <span class="small muted">共 ${esc(String(extra.total || 0))} 条 · 本次新增 ${esc(String(extra.fresh || 0))} 条</span>
        <span class="spacer"></span>
        ${extra.firstRun ? '<span class="small muted">首次进入：已把现有消息标记为已见</span>' : ''}
        <button class="btn btn--sm" data-act="ackAll">全部标记已读</button>
      </div>`;
    return;
  }

  const map = {
    loading: ['<span class="badge">读取中</span>', '正在向后端请求增量…'],
    ok: ['<span class="badge badge--ok">在线</span>', `最近同步 ${synced ? fmtTime(synced, true) : '—'} · 本次新增 ${ui.newCount} 条 · 翻了 ${ui.pages} 页`],
    empty: ['<span class="badge badge--ok">在线</span>', `已是最新：暂时没有新通知（最近同步 ${synced ? fmtTime(synced, true) : '—'}）`],
    offline: ['<span class="badge badge--danger">断线</span>', '当前无法连接网络，显示的是上次拉到的内容；恢复后会自动补拉'],
    forbidden: ['<span class="badge badge--danger">无权限 403</span>', '当前账号不能读取通知：可能不在群授权范围内，或授权已失效'],
    auth: ['<span class="badge badge--danger">未登录 401</span>', '登录已失效，请重新登录'],
    error: ['<span class="badge badge--danger">请求失败</span>', (ui.lastError && (ui.lastError.code || ui.lastError.message)) || '未知错误'],
  };
  const [badge, text] = map[ui.state] || map.loading;
  host.innerHTML = `<div class="notif-toolbar" style="margin-bottom:10px">${badge}
    <span class="small muted">${esc(text)}</span></div>`;
}

function markNav(items) {
  const unread = items.filter((n) => n.unread).length;
  const dot = document.getElementById('nav-notif-dot');
  if (dot) dot.textContent = unread ? String(unread) : '';
}

/* ---------------------------------------------------------------- 列表 */
function normalizeNotifs(data) {
  const list = Array.isArray(data) ? data : data.items || data.notifications || data.data || [];
  return list.map((n) => ({
    id: String(n.message_id || n.id || ''),
    groupKey: String(n.group_key || n.group_label || ''),
    group: String(n.group_name || n.group_label || n.group_key || '未知来源'),
    content: String(n.content || n.summary || n.text || ''),
    sentAt: n.sent_at || '',
    importedAt: n.imported_at || '',
    kind: (n.metadata && n.metadata.revision_kind) || n.revision_kind || 'initial',
    eventKey: (n.metadata && n.metadata.event_key) || n.event_key || '',
    unread: n.unread !== false && n.read !== true,
  }));
}

function listHtml(items, remote) {
  return `
    <section class="card">
      <div class="card__head">
        <h2>通知</h2>
        <span class="badge">${items.length} 条</span>
        <span class="spacer"></span>
        <span class="small muted">${remote ? '来源：GET /v1/notifications' : '来源：本地增量视图'}</span>
      </div>
      <ul class="notif-list">
        ${items
          .map(
            (n) => `<li class="notif ${n.unread ? 'is-unread' : ''}">
              <div class="notif__main">
                <div class="notif__meta">
                  ${n.unread ? '<span class="notif__dot" aria-label="未读"></span>' : ''}
                  <span class="chip">${esc(n.group)}</span>
                  ${kindBadge(n.kind)}
                  ${n.eventKey ? `<button class="link-btn mono" data-event="${esc(n.eventKey)}">事件 ${esc(n.eventKey)}</button>` : ''}
                  <span class="spacer"></span>
                  <span class="small muted" title="${esc(n.sentAt || '')}">
                    ${n.sentAt ? esc(fmtTime(n.sentAt)) : '<span class="badge badge--warn">时间未知</span>'}
                  </span>
                </div>
                <div class="notif__content">${esc(truncate(n.content, 160))}</div>
                <div class="small muted">导入 ${esc(relTime(n.importedAt))}</div>
              </div>
              <div class="notif__actions">
                <button class="btn btn--sm" data-open="${esc(n.id)}">查看原文</button>
                ${n.unread ? `<button class="btn btn--sm btn--ghost" data-read="${esc(n.id)}">标为已读</button>` : ''}
              </div>
            </li>`,
          )
          .join('')}
      </ul>
    </section>`;
}

function kindBadge(kind) {
  if (kind === 'correction') return '<span class="badge badge--warn">更正</span>';
  if (kind === 'cancellation') return '<span class="badge badge--danger">取消</span>';
  return '';
}

function bindList(root, ctx, items, remote) {
  root.querySelectorAll('[data-open]').forEach((b) => {
    b.onclick = () =>
      openMessageDrawer({
        api: ctx.api,
        messageId: b.dataset.open,
        onOpenFull: (id) => ctx.openDetail(id),
        onChanged: () => refresh(root, ctx, { manual: false }),
        onSessionsInvalidated: () => ctx.clearSessions('原文已被修订或删除，旧检索会话已失效'),
      });
  });
  root.querySelectorAll('[data-event]').forEach((b) => {
    b.onclick = () => {
      const first = items.find((n) => n.eventKey === b.dataset.event);
      if (first) ctx.openDetail(first.id);
    };
  });
  root.querySelectorAll('[data-read]').forEach((b) => {
    b.onclick = async () => {
      const target = items.find((n) => n.id === b.dataset.read);
      b.disabled = true;
      if (remote && target) {
        try {
          const r = await ctx.api.ackNotifications(target.groupKey, loadCursor());
          if (!r.available) toast('后端尚未提供已读接口（404），本次只在本机标记', 'warn');
          else toast('已同步到后端', 'ok');
        } catch (err) {
          toast(`已读上报失败（${err.code || err.status}）：${err.message}`, 'error');
        }
      } else {
        const latest = items.map((n) => n.importedAt || '').reduce((a, b) => (a > b ? a : b), '');
        localStorage.setItem(LS_LOCAL_ACK, latest);
        toast('已标记为已读（仅本机）', 'ok');
      }
      const li = b.closest('.notif');
      if (li) li.classList.remove('is-unread');
      b.remove();
    };
  });
}

/* ---------------------------------------------------------------- 小组件 */
function emptyHtml(remote) {
  return `<div class="empty"><span class="empty__icon">◎</span>
    <div style="margin:6px 0">暂时没有新通知</div>
    <div class="small">${remote
      ? '后端没有返回新消息。可以在已授权的测试群里发一条，再点「立即刷新」。'
      : '新消息导入后会自动出现在这里。'}</div></div>`;
}

function offlineHtml() {
  return `<div class="empty"><span class="empty__icon">⚡</span>
    <div style="margin:6px 0">当前断网</div>
    <div class="small">网络恢复后会自动补拉，不需要刷新页面。</div></div>`;
}

function errorHtml(err) {
  const status = err && err.status ? `（HTTP ${err.status}）` : '';
  const hint =
    err && err.status === 403
      ? '这不是"没有新消息"，而是没有读取权限：请确认账号在群授权范围内。'
      : err && err.status === 404
        ? '通知接口尚未实现（404），本页应自动降级为消息增量视图。'
        : err && err.code === 'TIMEOUT'
          ? '请求超时：后端没有在时限内响应，可以点「立即刷新」重试。'
          : '可以点「立即刷新」重试；持续失败请看「联调检查」页。';
  return `<div class="notice notice--danger"><span class="notice__icon">✕</span>
    <div><strong>${esc((err && err.code) || 'ERROR')}</strong>${esc(status)}：${esc((err && err.message) || '读取失败')}
      <div class="small" style="margin-top:4px">${esc(hint)}</div>
      ${err && err.requestId ? `<div class="small mono muted">request_id: ${esc(err.requestId)}</div>` : ''}
    </div></div>`;
}
