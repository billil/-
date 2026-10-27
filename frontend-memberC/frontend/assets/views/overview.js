/* 概览页：覆盖范围 + 事件 + 快捷入口 */
import { esc, fmtDay, fmtTime, toast } from '../util.js';

/** 与后端 samples/messages.json 一致的模拟数据（显著标注「模拟数据」） */
const SAMPLE = {
  idempotency_key: 'ui-sample-' + new Date().toISOString().slice(0, 10),
  messages: [
    {
      group_label: '模拟班级群',
      sent_at: '2026-10-26T09:00:00+08:00',
      content: '【模拟数据】外出志愿服务：11月1日09:00在东门集合，10月30日18:00前交报名表。',
      metadata: {
        event_key: 'service',
        title: '外出志愿服务',
        source_confirmed: true,
        fields: { 时间: '11月1日09:00', 地点: '东门', 截止: '10月30日18:00' },
      },
    },
    {
      group_label: '模拟活动群',
      sent_at: '2026-10-27T09:00:00+08:00',
      content: '【模拟数据】校内志愿培训：11月2日14:00在讲堂参加培训。',
      metadata: {
        event_key: 'training',
        title: '校内志愿培训',
        source_confirmed: true,
        fields: { 时间: '11月2日14:00', 地点: '讲堂' },
      },
    },
    {
      group_label: '模拟课程群',
      content: '【模拟数据】程序设计课程作业：请交实验报告。',
    },
  ],
};

export async function render(root, ctx) {
  root.innerHTML = `<div class="empty"><span class="empty__icon">◌</span>正在读取覆盖范围…</div>`;

  let cov;
  let events = [];
  try {
    cov = await ctx.api.coverage();
    ctx.state.coverage = cov;
    ctx.refreshShell();
    const ev = await ctx.api.events().catch(() => ({ items: [] }));
    events = ev.items || [];
  } catch (err) {
    root.innerHTML = `<div class="notice notice--danger"><span class="notice__icon">✕</span>
      <div><strong>${esc(err.code)}</strong>：${esc(err.message)}
      ${err.hint ? `<div class="small" style="margin-top:4px">${esc(err.hint)}</div>` : ''}</div></div>`;
    ctx.handleError(err);
    return;
  }

  const span =
    cov.sent_start && cov.sent_end
      ? `${fmtDay(cov.sent_start)} → ${fmtDay(cov.sent_end)}`
      : '暂无可用于筛选的发送时间';

  root.innerHTML = `
    <div class="page-head">
      <div>
        <h2>数据覆盖范围</h2>
        <p>只代表当前令牌所属数据空间里<strong>已导入</strong>的内容，不等于“某个时间段的消息都收全了”。
        时间口径是消息的<strong>发送时间</strong>，不是导入时间。</p>
      </div>
    </div>

    <div class="grid grid--4" style="margin-bottom:14px">
      <div class="stat">
        <div class="stat__label">已导入消息</div>
        <div class="stat__value">${cov.imported_count ?? 0}</div>
        <div class="stat__meta">其中 ${cov.unknown_time_count ?? 0} 条没有发送时间</div>
      </div>
      <div class="stat">
        <div class="stat__label">已建索引</div>
        <div class="stat__value">${cov.indexed_count ?? 0}</div>
        <div class="stat__meta">${cov.indexed_count === cov.imported_count ? '全部可检索' : '存在未索引记录，可重建索引'}</div>
      </div>
      <div class="stat">
        <div class="stat__label">群标签</div>
        <div class="stat__value">${(cov.groups || []).length}</div>
        <div class="stat__meta">${esc((cov.groups || []).slice(0, 2).join('、') || '尚未导入')}</div>
      </div>
      <div class="stat">
        <div class="stat__label">事件数（已标注）</div>
        <div class="stat__value">${events.length}</div>
        <div class="stat__meta">${events.filter((e) => e.cancelled).length} 个已取消</div>
      </div>
    </div>

    <div class="grid grid--2">
      <section class="card">
        <div class="card__head">
          <h2>时间跨度与来源</h2>
          <span class="spacer"></span>
          <span class="small muted">发送时间</span>
        </div>
        <div class="card__body stack stack--sm">
          <div class="row"><span class="badge badge--info">${esc(span)}</span></div>
          <div class="row" style="gap:6px">
            ${(cov.groups || []).map((g) => `<span class="chip">${esc(g)}</span>`).join('') || '<span class="muted small">还没有群标签</span>'}
          </div>
          <div class="notice notice--muted small"><span class="notice__icon">i</span>
            <div>${esc(cov.scope || '')}</div></div>
        </div>
      </section>

      <section class="card">
        <div class="card__head"><h2>快捷操作</h2></div>
        <div class="card__body stack stack--sm">
          <button class="btn btn--block" data-act="sample">导入示例数据（3 条模拟消息）</button>
          <button class="btn btn--block" data-act="import">自己粘贴通知导入</button>
          <button class="btn btn--block btn--primary" data-act="ask">去智能查找提问</button>
          <button class="btn btn--block" data-act="rebuild">重建全部索引</button>
          <div class="small muted">当前后端为 <code>${esc(ctx.state.capabilities?.mode || '—')}</code> 模式，
            检索器 <code>${esc(ctx.state.capabilities?.search || '—')}</code>。</div>
        </div>
      </section>
    </div>

    <section class="card" style="margin-top:14px">
      <div class="card__head">
        <h2>已标注事件</h2>
        <span class="sub">来自 GET /v1/events，含更正关系与冲突标记</span>
        <span class="spacer"></span>
        <span class="badge">${events.length} 个</span>
      </div>
      ${
        events.length
          ? `<div class="table-wrap"><table class="table">
              <thead><tr><th>事件</th><th>消息</th><th>字段</th><th>状态</th><th></th></tr></thead>
              <tbody>
                ${events
                  .map((e) => {
                    const names = Object.keys(e.fields || {});
                    const conflict = (e.conflicts || []).length > 0;
                    return `<tr>
                      <td><strong>${esc(e.title || e.event_key)}</strong><div class="small muted mono">${esc(e.event_key)}</div></td>
                      <td>${(e.messages || []).length} 条</td>
                      <td class="small">${names.length ? esc(names.join('、')) : '<span class="muted">无</span>'}</td>
                      <td>
                        ${e.cancelled ? '<span class="badge badge--warn">已取消</span>' : '<span class="badge badge--ok">有效</span>'}
                        ${conflict ? '<span class="badge badge--danger">字段冲突</span>' : ''}
                      </td>
                      <td class="nowrap"><button class="btn btn--sm" data-ask-event="${esc(e.event_key)}">按此事件提问</button></td>
                    </tr>`;
                  })
                  .join('')}
              </tbody></table></div>`
          : `<div class="empty"><span class="empty__icon">◌</span>还没有带 <code>event_key</code> 的消息，导入时加上事件标注即可出现</div>`
      }
    </section>
  `;

  root.querySelector('[data-act="sample"]').onclick = async (e) => {
    const btn = e.currentTarget;
    btn.disabled = true;
    try {
      const res = await ctx.api.importMessages(SAMPLE);
      toast(`示例数据：成功 ${res.success_count} 条${res.replayed ? '（幂等重放）' : ''}`, 'ok');
      await render(root, ctx);
    } catch (err) {
      toast(`${err.code}：${err.message}`, 'error');
      ctx.handleError(err);
    } finally {
      btn.disabled = false;
    }
  };
  root.querySelector('[data-act="import"]').onclick = () => ctx.openImport(() => render(root, ctx));
  root.querySelector('[data-act="ask"]').onclick = () => ctx.navigate('#/search');
  root.querySelector('[data-act="rebuild"]').onclick = async (e) => {
    const btn = e.currentTarget;
    btn.disabled = true;
    try {
      const res = await ctx.api.rebuildIndex();
      toast(`已重建 ${res.indexed} 条索引`, 'ok');
      await render(root, ctx);
    } catch (err) {
      toast(`${err.code}：${err.message}`, 'error');
    } finally {
      btn.disabled = false;
    }
  };
  root.querySelectorAll('[data-ask-event]').forEach((b) => {
    b.onclick = () => {
      ctx.navigate('#/search');
      ctx.state.pendingEventKey = b.dataset.askEvent;
    };
  });
}
