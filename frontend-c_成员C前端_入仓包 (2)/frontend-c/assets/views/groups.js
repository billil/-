/* 群管理与关注：群列表、授权状态、关注/取消关注
 *
 * 依赖 GET /v1/groups、GET/POST /v1/subscriptions、DELETE /v1/subscriptions/{key}
 * —— 这些接口在当前后端（member-d/backend）中**尚未实现**，调用会 404。
 * 本页的做法：接口就绪就渲染真实数据；未就绪就在原位说明「等后端上线后自动可用」，
 * 并给出**能用既有接口做到的部分**（按群标签看覆盖范围、直连该群的消息）。
 * 不摆假开关、不伪造关注状态。
 */
import { esc, fmtTime, toast } from '../util.js';

const ui = { busy: false, selected: new Set(), lastGroups: null };

export async function render(root, ctx) {
  root.innerHTML = `<div class="empty"><span class="empty__icon">◌</span>正在读取群信息…</div>`;

  const [gRes, sRes] = await Promise.all([
    ctx.api.groups().catch((err) => ({ available: false, reason: 'ERROR', error: err })),
    ctx.api.subscriptions().catch((err) => ({ available: false, reason: 'ERROR', error: err })),
  ]);

  const groups = gRes.available ? normalizeGroups(gRes.data) : [];
  const subscribed = new Set(sRes.available ? normalizeSubs(sRes.data) : []);
  const coverage = ctx.state.coverage || {};
  const covGroups = coverage.groups || [];

  if (gRes.available) {
    ui.lastGroups = groups;
    ui.selected = new Set(subscribed);
  }

  root.innerHTML = `
    <div class="page-head">
      <div>
        <h2>群管理</h2>
        <p>关注哪些群，决定通知中心给你推送什么。关注关系保存在<strong>后端</strong>，换设备、重开页面都还在。</p>
      </div>
      <span class="spacer"></span>
      <button class="btn btn--sm" data-act="reload">重新读取</button>
    </div>

    ${renderReadiness(gRes, sRes)}

    ${groups.length ? renderGroups(groups, subscribed, ctx) : renderFromCoverage(covGroups, coverage, ctx)}
  `;

  root.querySelector('[data-act="reload"]').onclick = () => render(root, ctx);

  const importBtn = root.querySelector('[data-act="import"]');
  if (importBtn) importBtn.onclick = () => ctx.openImport(() => render(root, ctx));

  // 未就绪时也允许「按群标签查看」——用现有 /v1/messages 能力做，不假装是新接口
  root.querySelectorAll('[data-goto-group]').forEach((b) => {
    b.onclick = () => {
      ctx.navigate('#/library');
      setTimeout(() => {
        const sel = document.querySelector('[data-group]');
        if (sel) {
          sel.value = b.dataset.gotoGroup;
          sel.dispatchEvent(new Event('change'));
        }
      }, 200);
    };
  });

  if (!groups.length) return;

  // 全选 / 清空
  const all = root.querySelector('[data-act="all"]');
  if (all) {
    all.onclick = () => {
      root.querySelectorAll('[data-pick]').forEach((el) => {
        el.checked = true;
      });
      ui.selected = new Set(groups.map((g) => g.key));
    };
  }
  root.querySelectorAll('[data-pick]').forEach((el) => {
    el.onchange = () => {
      const k = el.dataset.pick;
      if (el.checked) ui.selected.add(k);
      else ui.selected.delete(k);
    };
  });

  const saveBtn = root.querySelector('[data-act="save"]');
  if (saveBtn) {
    saveBtn.onclick = async () => {
      if (ui.busy) return;
      ui.busy = true;
      saveBtn.disabled = true;
      saveBtn.textContent = '保存中…';
      try {
        const keys = Array.from(ui.selected);
        const r = await ctx.api.subscribe(keys);
        if (!r.available) {
          toast('后端尚未提供 /v1/subscriptions，关注无法保存', 'warn');
        } else {
          toast(`已保存关注：${keys.length} 个群`, 'ok');
          render(root, ctx);
        }
      } catch (err) {
        toast(`${err.code}：${err.message}`, 'error');
        ctx.handleError(err);
      } finally {
        ui.busy = false;
        if (saveBtn) {
          saveBtn.disabled = false;
          saveBtn.textContent = '保存关注';
        }
      }
    };
  }

  // 单群取消关注（DELETE /v1/subscriptions/{key}）
  root.querySelectorAll('[data-unsub]').forEach((b) => {
    b.onclick = async () => {
      const key = b.dataset.unsub;
      b.disabled = true;
      try {
        const r = await ctx.api.unsubscribe(key);
        if (!r.available) toast('后端尚未提供退订接口', 'warn');
        else {
          toast('已取消关注', 'ok');
          render(root, ctx);
        }
      } catch (err) {
        toast(`${err.code}：${err.message}`, 'error');
      } finally {
        b.disabled = false;
      }
    };
  });
}

/* ---------------------------------------------------------------- 渲染块 */

function renderReadiness(gRes, sRes) {
  const items = [
    ['GET /v1/groups', gRes],
    ['GET /v1/subscriptions', sRes],
  ];
  const allReady = items.every(([, r]) => r.available);
  if (allReady) {
    return `<div class="notice notice--ok" style="margin-bottom:14px"><span class="notice__icon">✓</span>
      <div>后端已提供群与订阅接口，本页显示的是<strong>后端真实状态</strong>。</div></div>`;
  }
  return `<div class="notice notice--info" style="margin-bottom:14px"><span class="notice__icon">i</span>
    <div>
      <div style="margin-bottom:6px">当前后端还<strong>没有</strong>这些接口，本页不会伪造关注状态：</div>
      <div class="integration">
        ${items
          .map(
            ([name, r]) => `<span class="integration__item ${r.available ? 'is-ready' : ''}">
              <span class="integration__dot"></span>${esc(name)}
              <span class="integration__state">${r.available ? '已就绪' : r.reason === 'NOT_IMPLEMENTED' ? '待后端实现（404）' : '请求失败'}</span>
            </span>`,
          )
          .join('')}
      </div>
      <div class="small muted" style="margin-top:8px">接口上线后本页会自动切换为真实数据，前端无需再改代码。</div>
    </div></div>`;
}

function renderGroups(groups, subscribed, ctx) {
  return `
    <section class="card">
      <div class="card__head">
        <h2>可访问的群</h2>
        <span class="badge">${groups.length} 个</span>
        <span class="spacer"></span>
        <button class="btn btn--sm" data-act="all">全选</button>
        <button class="btn btn--sm btn--primary" data-act="save">保存关注</button>
      </div>
      <div class="table-wrap">
        <table class="table">
          <thead><tr>
            <th style="width:52px">关注</th><th>群名称</th><th style="width:110px">来源</th>
            <th style="width:110px">授权</th><th style="width:150px">最后接收</th><th style="width:90px"></th>
          </tr></thead>
          <tbody>
            ${groups
              .map(
                (g) => `<tr>
                  <td><input type="checkbox" data-pick="${esc(g.key)}" ${subscribed.has(g.key) ? 'checked' : ''}
                      ${g.authorized ? '' : 'disabled'} aria-label="关注 ${esc(g.name)}" /></td>
                  <td>
                    <div class="row" style="gap:6px">
                      <strong>${esc(g.name)}</strong>
                      ${g.status ? `<span class="badge">${esc(g.status)}</span>` : ''}
                    </div>
                    <div class="small muted mono">${esc(g.key)}</div>
                  </td>
                  <td>${esc(g.source || '—')}</td>
                  <td>${g.authorized
                    ? '<span class="badge badge--ok">已授权</span>'
                    : '<span class="badge badge--warn">未授权</span>'}</td>
                  <td class="small">${g.lastReceived ? esc(fmtTime(g.lastReceived)) : '—'}</td>
                  <td>${subscribed.has(g.key)
                    ? `<button class="btn btn--sm btn--ghost" data-unsub="${esc(g.key)}">取消</button>`
                    : ''}</td>
                </tr>`,
              )
              .join('')}
          </tbody>
        </table>
      </div>
    </section>`;
}

function renderFromCoverage(covGroups, coverage, ctx) {
  if (!covGroups.length) {
    return `<div class="empty"><span class="empty__icon">◎</span>
      <div style="margin:6px 0">还没有任何群数据</div>
      <div class="small">先在消息库里导入几条带群标签的消息，这里就会列出你已导入的群。</div>
      <button class="btn btn--sm" style="margin-top:12px" data-act="import">去导入</button></div>`;
  }
  return `
    <section class="card">
      <div class="card__head"><h2>已导入内容涉及的群</h2>
        <span class="badge">${covGroups.length} 个</span></div>
      <div class="card__body">
        <div class="small muted" style="margin-bottom:10px">这些群来自 <code>GET /v1/coverage</code> 的
          <code>groups</code> 字段——是<strong>已导入消息</strong>里出现过的群标签，不等于"已接入的 QQ 群"。
          消息共 ${esc(String(coverage.imported_count ?? '?'))} 条，已建索引 ${esc(String(coverage.indexed_count ?? '?'))} 条。</div>
        <div class="chips">
          ${covGroups
            .map(
              (g) => `<button class="chip chip--btn" data-goto-group="${esc(g)}">${esc(g)} →</button>`,
            )
            .join('')}
        </div>
        <div class="small muted" style="margin-top:12px">点任意群，会跳到消息库并按该群筛选。</div>
      </div>
    </section>`;
}

/* ---------------------------------------------------------------- 归一化 */
/* 后端字段名尚未冻结（接口还没实现），这里做宽容归一，避免上线后字段名小改就崩。 */

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
  const list = Array.isArray(data) ? data : data.items || data.subscriptions || data.groups || data.data || [];
  return list.map((s) => (typeof s === 'string' ? s : String(pick(s, 'group_key', 'key', 'group_label', 'id'))));
}
