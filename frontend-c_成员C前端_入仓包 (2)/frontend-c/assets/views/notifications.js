/* 通知中心：增量拉取 + 游标 + 去重 + 已读
 *
 * 依赖 GET /v1/notifications、POST /v1/notifications/ack —— 当前后端尚未实现（404）。
 * 未就绪时，本页用**已有接口**给出等价的可用视图：
 *   /v1/messages 的增量轮询（按 imported_at 增量、按 id 去重）+ 本地已读游标。
 * 并在页头明确标注这两条路径的区别，绝不把本地轮询说成"后端推送"。
 */
import { esc, fmtTime, relTime, toast, truncate } from '../util.js';
import { openMessageDrawer } from '../message-actions.js';

const LS_SEEN = 'qunyi.notif.seen';       // 已见过的 message_id（去重）
const LS_CURSOR = 'qunyi.notif.cursor';   // 后端 next_cursor（接口就绪时才用）
const LS_LOCAL_ACK = 'qunyi.notif.localAck'; // 未就绪时的本地已读导入时间

const ui = {
  timer: null,
  polling: false,
  refreshing: false,
  intervalMs: 30000,
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
    localStorage.setItem(LS_SEEN, JSON.stringify(Array.from(set).slice(-800)));
  } catch {
    /* 配额满了就算了，去重是增强不是核心 */
  }
}
function loadLocalAck() {
  return localStorage.getItem(LS_LOCAL_ACK) || '';
}

/* ---------------------------------------------------------------- 渲染 */

export async function render(root, ctx) {
  clearTimer();

  root.innerHTML = `
    <div class="page-head">
      <div>
        <h2>通知中心</h2>
        <p>新消息进来时在这里增量出现。同一条消息<strong>只出现一次</strong>，标记已读后重新打开也不会变回未读。</p>
      </div>
      <span class="spacer"></span>
      <button class="btn btn--sm" data-act="refresh">立即刷新</button>
    </div>
    <div id="notif-body"><div class="empty"><span class="empty__icon">◌</span>正在读取通知…</div></div>
  `;

  root.querySelector('[data-act="refresh"]').onclick = () => refresh(root, ctx, { manual: true });
  await refresh(root, ctx, { manual: false });

  // 30 秒自动刷新（页面隐藏时停，避免后台空转）
  ui.timer = setInterval(() => {
    if (document.visibilityState === 'visible') refresh(root, ctx, { manual: false });
  }, ui.intervalMs);

  // 路由切换时清理：由 app.js 在重渲染前调用 dispose
  ctx.__disposeNotif = clearTimer;
}

export function dispose() {
  clearTimer();
}

function clearTimer() {
  if (ui.timer) {
    clearInterval(ui.timer);
    ui.timer = null;
  }
}

/* ---------------------------------------------------------------- 刷新 */

async function refresh(root, ctx, { manual }) {
  const body = root.querySelector('#notif-body');
  if (!body || ui.refreshing) return;
  ui.refreshing = true;
  if (manual) body.classList.add('is-loading');

  try {
    const res = await ctx.api.notifications({ after: loadCursor(), limit: 50 }).catch((err) => ({
      available: false,
      reason: 'ERROR',
      error: err,
    }));

    if (res.available) {
      renderRemote(body, root, ctx, res.data);
    } else if (res.reason === 'NOT_IMPLEMENTED') {
      await renderLocalPoll(body, root, ctx);
    } else {
      body.innerHTML = errorBlock(res.error, ctx);
    }
  } catch (err) {
    body.innerHTML = errorBlock(err, ctx);
  } finally {
    ui.refreshing = false;
    body.classList.remove('is-loading');
  }
}

function loadCursor() {
  return localStorage.getItem(LS_CURSOR) || '';
}

/* ------------------------------------------ 分支一：后端通知接口已就绪 */

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

function renderRemote(body, root, ctx, data) {
  const items = normalizeNotifs(data);
  const next = data.next_cursor || data.cursor || '';
  if (next) localStorage.setItem(LS_CURSOR, next);

  body.innerHTML = `
    ${banner('后端通知接口已就绪：本页数据来自 <code>GET /v1/notifications</code>，游标由后端保存。', 'ok')}
    ${next ? `<div class="small muted" style="margin-bottom:8px">next_cursor：<code>${esc(String(next))}</code></div>` : ''}
    ${items.length ? listHtml(items, ctx, true) : emptyHtml()}
  `;
  bindList(root, ctx, items, true);
}

/* ------------------------------- 分支二：后端未实现，用消息增量做等价视图 */

async function renderLocalPoll(body, root, ctx) {
  // 拉最近 100 条，按 imported_at 增量 + id 去重
  const page = await ctx.api.listMessages({ limit: 100, offset: 0 });
  const all = page.items || [];
  const seen = loadSeen();
  const localAck = loadLocalAck();

  // 首次进入：把已有消息全部标记为"已见"，避免一下子刷出 100 条"新通知"
  const firstRun = seen.size === 0 && !localAck;
  const fresh = firstRun ? [] : all.filter((m) => !seen.has(m.id));

  all.forEach((m) => seen.add(m.id));
  saveSeen(seen);

  const items = (fresh.length ? fresh : all).map((m) => ({
    id: m.id,
    groupKey: m.group_label,
    group: m.group_label || '未知来源',
    content: m.content || '',
    sentAt: m.sent_at,
    importedAt: m.imported_at,
    kind: (m.metadata && m.metadata.revision_kind) || 'initial',
    eventKey: (m.metadata && m.metadata.event_key) || '',
    unread: !localAck || (m.imported_at || '') > localAck,
  }));

  body.innerHTML = `
    ${banner(
      '后端尚未提供 <code>/v1/notifications</code>。下面的列表由 <code>GET /v1/messages</code> 增量轮询得到（每 30 秒），' +
        '已读状态存在本机浏览器，<strong>不是</strong>服务端推送，换设备不会同步。接口上线后本页自动切换。',
      'info',
    )}
    <div class="notif-toolbar">
      <span class="badge badge--warn">本地增量视图</span>
      <span class="small muted">共 ${all.length} 条 · 本轮新增 ${fresh.length} 条</span>
      <span class="spacer"></span>
      ${firstRun ? '<span class="small muted">首次进入，已把现有消息标记为已见</span>' : ''}
      <button class="btn btn--sm" data-act="ackAll">全部标记已读</button>
    </div>
    ${items.length ? listHtml(items, ctx, false) : emptyHtml()}
  `;

  const ackAll = body.querySelector('[data-act="ackAll"]');
  if (ackAll) {
    ackAll.onclick = () => {
      const latest = all.reduce((acc, m) => ((m.imported_at || '') > acc ? m.imported_at : acc), '');
      localStorage.setItem(LS_LOCAL_ACK, latest || new Date().toISOString());
      toast('已把当前所有通知标记为已读（仅本机）', 'ok');
      refresh(root, ctx, { manual: false });
    };
  }
  bindList(root, ctx, items, false);
}

/* ---------------------------------------------------------------- 列表 */

function listHtml(items, ctx, remote) {
  return `
    <section class="card">
      <div class="card__head">
        <h2>通知</h2>
        <span class="badge">${items.length} 条</span>
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
        // 抽屉里点「查看完整页面」→ 跳详情页
        onOpenFull: (id) => ctx.openDetail(id),
        // 修订/删除会改变列表，重新拉一次
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
          if (!r.available) toast('后端尚未提供已读接口', 'warn');
          else toast('已标记', 'ok');
        } catch (err) {
          toast(`${err.code}：${err.message}`, 'error');
        }
      } else {
        const latest = items
          .map((n) => n.importedAt || '')
          .reduce((a, b) => (a > b ? a : b), '');
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

function banner(text, tone) {
  const icon = tone === 'ok' ? '✓' : 'i';
  return `<div class="notice notice--${tone}" style="margin-bottom:12px"><span class="notice__icon">${icon}</span>
    <div>${text}</div></div>`;
}

function emptyHtml() {
  return `<div class="empty"><span class="empty__icon">◎</span>
    <div style="margin:6px 0">还没有通知</div>
    <div class="small">新消息导入后会自动出现在这里。</div></div>`;
}

function errorBlock(err, ctx) {
  if (err && ctx) ctx.handleError(err);
  return `<div class="notice notice--danger"><span class="notice__icon">✕</span>
    <div><strong>${esc((err && err.code) || 'ERROR')}</strong>：${esc((err && err.message) || '读取失败')}</div></div>`;
}
