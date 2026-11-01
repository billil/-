/* 群管理与关注
 *
 * 真实接口：GET /v1/groups、GET /v1/subscriptions、
 *           POST /v1/subscriptions（body: {group_keys: [...]}）、
 *           DELETE /v1/subscriptions/{group_key}
 *
 * 关键规则（任务清单 P0-2）：
 *   - 关注状态以**后端返回**为准，不在本地假装保存成功
 *   - 每次勾选立即提交，失败就回滚到原状态并说明原因
 *   - 403 / 404 / 超时 分开提示
 *   - 勾选 ≠ 拥有群访问权限；未授权群不给阅读入口
 */
import { esc, fmtTime, toast } from '../util.js';

const ui = { busyKey: '', saving: false };

export async function render(root, ctx) {
  root.innerHTML = `<div class="empty"><span class="empty__icon">◌</span>正在读取群信息…</div>`;

  if (ctx.state.conn !== 'backend') {
    root.innerHTML = localHint(ctx);
    const b = root.querySelector('[data-act="login"]');
    if (b) b.onclick = () => ctx.navigate('#/integration');
    return;
  }

  const [gRes, sRes] = await Promise.all([
    ctx.api.groups().catch((err) => ({ available: false, reason: 'ERROR', error: err })),
    ctx.api.subscriptions().catch((err) => ({ available: false, reason: 'ERROR', error: err })),
  ]);

  const groups = gRes.available ? normalizeGroups(gRes.data) : [];
  const subscribed = new Set(sRes.available ? normalizeSubs(sRes.data) : []);

  root.innerHTML = `
    <div class="page-head">
      <div>
        <h2>我的群</h2>
        <p>关注哪些群，决定通知中心给你推送什么。关注关系保存在<strong>后端</strong>，
          换设备、重开页面、后端重启后都保持一致。</p>
      </div>
      <span class="spacer"></span>
      <button class="btn btn--sm" data-act="reload">重新读取</button>
    </div>

    ${renderReadiness(gRes, sRes)}
    ${groups.length ? renderGroups(groups, subscribed) : renderEmpty(gRes)}
  `;

  root.querySelector('[data-act="reload"]').onclick = () => render(root, ctx);

  root.querySelectorAll('[data-pick]').forEach((el) => {
    el.onchange = () => toggle(root, ctx, el, groups, subscribed);
  });
  root.querySelectorAll('[data-unsub]').forEach((btn) => {
    btn.onclick = () => unsubscribe(root, ctx, btn.dataset.unsub);
  });
}

/* ------------------------------------------------------------ 关注 / 取消 */
async function toggle(root, ctx, el, groups, subscribed) {
  const key = el.dataset.pick;
  const want = el.checked;
  const row = el.closest('tr');
  const before = !want; // 原来的状态
  setRowBusy(row, true, want ? '保存中…' : '取消中…');

  try {
    const r = await ctx.api.subscribe(keysAfter(groups, subscribed, key, want));
    if (!r.available) {
      throw makeErr(404, 'NOT_IMPLEMENTED', '后端尚未提供 POST /v1/subscriptions');
    }
    if (want) subscribed.add(key);
    else subscribed.delete(key);
    toast(want ? '已关注，保存在后端' : '已取消关注', 'ok');
    await render(root, ctx);
  } catch (err) {
    el.checked = before;
    setRowBusy(row, false);
    toast(subscribeError(err, want), 'error', 6000);
    ctx.handleError(err);
  } finally {
    setRowBusy(row, false);
  }
}

async function unsubscribe(root, ctx, key) {
  const row = root.querySelector(`tr[data-row="${cssEsc(key)}"]`);
  setRowBusy(row, true, '取消中…');
  try {
    const r = await ctx.api.unsubscribe(key);
    if (!r.available) throw makeErr(404, 'NOT_IMPLEMENTED', '后端尚未提供 DELETE /v1/subscriptions/{group_key}');
    toast('已取消关注', 'ok');
    await render(root, ctx);
  } catch (err) {
    toast(subscribeError(err, false), 'error', 6000);
    ctx.handleError(err);
  } finally {
    setRowBusy(row, false);
  }
}

/** 后端接收整个 group_keys 数组，所以每次提交「当前最新的一组」 */
function keysAfter(groups, subscribed, key, want) {
  const next = new Set(subscribed);
  if (want) next.add(key);
  else next.delete(key);
  // 只提交后端认可的群，避免把未授权 key 带上去被整体拒绝
  return groups.filter((g) => next.has(g.key) && g.authorized).map((g) => g.key);
}

function subscribeError(err, want) {
  const action = want ? '关注' : '取消关注';
  if (err.status === 403) return `无权限${action}（403）：这个群不在你的授权范围内，请联系管理员或重新授权后端账号`;
  if (err.status === 404) return `后端尚未提供关注接口（404），本次${action}没有保存`;
  if (err.status === 401) return '登录已失效（401），请重新登录';
  if (err.code === 'TIMEOUT') return `${action}超时：后端没有在时限内响应，已恢复原状态`;
  if (err.status === 0) return `连不上后端，${action}未保存，已恢复原状态`;
  return `${action}失败（${err.code || err.status}）：${err.message || '未知原因'}，已恢复原状态`;
}

function makeErr(status, code, message) {
  const e = new Error(message);
  e.status = status;
  e.code = code;
  return e;
}

function setRowBusy(row, busy, label) {
  if (!row) return;
  row.classList.toggle('is-busy', Boolean(busy));
  const cell = row.querySelector('[data-state]');
  if (cell) cell.textContent = busy ? label || '保存中…' : '';
}

function cssEsc(s) {
  return String(s).replace(/["\\]/g, '\\$&');
}

/* ---------------------------------------------------------------- 渲染块 */

function localHint() {
  return `<div class="empty" style="padding:56px 20px">
    <span class="empty__icon">◎</span>
    <div style="margin:6px 0">群列表需要后端</div>
    <div class="small muted" style="max-width:460px;margin:0 auto">
      当前是本地试用，没有可访问群、也没有关注关系——本地试用只认你手动导入的原文，
      不能代表真实 QQ 群。连上后端后，这里会列出后端返回的、你有权访问的群。</div>
    <button class="btn btn--primary btn--sm" style="margin-top:14px" data-act="login">去连接后端</button>
  </div>`;
}

function renderReadiness(gRes, sRes) {
  const items = [
    ['GET /v1/groups', gRes],
    ['GET /v1/subscriptions', sRes],
  ];
  const allReady = items.every(([, r]) => r.available);
  if (allReady) {
    return `<div class="notice notice--ok" style="margin-bottom:14px"><span class="notice__icon">✓</span>
      <div>群与关注接口可用，本页显示的是<strong>后端真实状态</strong>。</div></div>`;
  }
  return `<div class="notice notice--warn" style="margin-bottom:14px"><span class="notice__icon">!</span>
    <div>
      <div style="margin-bottom:6px">部分接口不可用，本页<strong>不会伪造关注状态</strong>：</div>
      <div class="integration">
        ${items
          .map(
            ([name, r]) => `<span class="integration__item ${r.available ? 'is-ready' : ''}">
              <span class="integration__dot"></span>${esc(name)}
              <span class="integration__state">${r.available ? '已就绪' : r.reason === 'NOT_IMPLEMENTED' ? '未实现（404）' : describeErr(r.error)}</span>
            </span>`,
          )
          .join('')}
      </div>
      <div class="small muted" style="margin-top:8px">详细状态见「联调检查」页，可导出记录交给后端。</div>
    </div></div>`;
}

function describeErr(err) {
  if (!err) return '请求失败';
  if (err.status === 401) return '未登录（401）';
  if (err.status === 403) return '无权限（403）';
  if (err.code === 'TIMEOUT') return '超时';
  if (err.status === 0) return '不可达';
  return `失败（${err.status || err.code}）`;
}

function renderGroups(groups, subscribed) {
  return `
    <section class="card">
      <div class="card__head">
        <h2>可访问的群</h2>
        <span class="badge">${groups.length} 个</span>
        <span class="badge badge--ok">已关注 ${subscribed.size} 个</span>
        <span class="spacer"></span>
        <span class="small muted">勾选即保存到后端</span>
      </div>
      <div class="table-wrap">
        <table class="table">
          <thead><tr>
            <th style="width:56px">关注</th><th>群名称</th><th style="width:100px">来源</th>
            <th style="width:100px">授权</th><th style="width:120px">同步</th>
            <th style="width:150px">最后接收</th><th style="width:80px"></th>
          </tr></thead>
          <tbody>
            ${groups.map((g) => rowHtml(g, subscribed)).join('')}
          </tbody>
        </table>
      </div>
      <div class="card__body">
        <div class="small muted">勾选只表示"我想关注"，<strong>不等于</strong>拥有该群成员权限；
          未授权群不提供阅读入口，服务端也会再校验一次。</div>
      </div>
    </section>`;
}

function rowHtml(g, subscribed) {
  const on = subscribed.has(g.key);
  return `<tr data-row="${esc(g.key)}">
    <td>
      <input type="checkbox" data-pick="${esc(g.key)}" ${on ? 'checked' : ''} ${g.authorized ? '' : 'disabled'}
        aria-label="关注 ${esc(g.name)}" />
    </td>
    <td>
      <div class="row" style="gap:6px"><strong>${esc(g.name)}</strong></div>
      <div class="small muted mono">${esc(g.key)}</div>
    </td>
    <td class="small">${esc(g.source || '—')}</td>
    <td>${g.authorized
      ? '<span class="badge badge--ok">已授权</span>'
      : '<span class="badge badge--warn" title="勾选不代表拥有访问权限">未授权</span>'}</td>
    <td class="small" data-state>${esc(g.status || '')}</td>
    <td class="small">${g.lastReceived ? esc(fmtTime(g.lastReceived)) : '—'}</td>
    <td>${on ? `<button class="btn btn--sm btn--ghost" data-unsub="${esc(g.key)}">取消</button>` : ''}</td>
  </tr>`;
}

function renderEmpty(gRes) {
  if (!gRes.available) {
    return `<div class="empty"><span class="empty__icon">◎</span>
      <div style="margin:6px 0">读不到群列表</div>
      <div class="small">后端尚未提供 <code>/v1/groups</code>（404），或当前账号没有可访问的群。</div></div>`;
  }
  return `<div class="empty"><span class="empty__icon">◎</span>
    <div style="margin:6px 0">还没有可访问的群</div>
    <div class="small">后端返回了空列表。确认机器人已加入群、且该群已授权给当前账号。</div></div>`;
}

/* ---------------------------------------------------------------- 归一化 */
function pick(obj, ...names) {
  for (const n of names) {
    if (obj && obj[n] !== undefined && obj[n] !== null && obj[n] !== '') return obj[n];
  }
  return '';
}

function normalizeGroups(data) {
  const list = Array.isArray(data) ? data : data.items || data.groups || data.data || [];
  return list.map((g) => ({
    key: String(pick(g, 'group_key', 'key', 'group_label', 'id', 'name')),
    name: String(pick(g, 'name', 'title', 'group_name', 'group_label') || pick(g, 'group_key', 'key', 'id')),
    source: String(pick(g, 'source', 'platform', 'channel')),
    status: String(pick(g, 'status', 'sync_status', 'state')),
    authorized: pick(g, 'authorized', 'permitted', 'accessible') !== false,
    lastReceived: pick(g, 'last_received_at', 'last_message_at', 'updated_at'),
  }));
}

function normalizeSubs(data) {
  // 后端契约里字段名是 group_keys（数组），同时也兼容 items/subscriptions 等写法
  const list = Array.isArray(data)
    ? data
    : data.group_keys || data.items || data.subscriptions || data.groups || data.data || [];
  return list.map((s) => (typeof s === 'string' ? s : String(pick(s, 'group_key', 'key', 'group_label', 'id'))));
}
