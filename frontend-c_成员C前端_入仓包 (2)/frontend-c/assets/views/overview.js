/* =========================================================================
 * overview.js —— 工作台（首屏）
 *
 * 打开就是干活的界面，不是看板：
 *   左：粘贴 / 文件导入 → 立刻建索引
 *   右：提问 → 答案 + 字段 + 原文引用
 *   下：最近导入与维护入口
 *
 * 数据落在浏览器本地（LocalEngine），所以刷新不丢、离线能用；
 * 若显式连了成员 D 的后端，则同一套界面走服务端接口。
 * ========================================================================= */

import { esc, fmtTime, toast, downloadJson, confirmDialog } from '../util.js';

const EXAMPLES = [
  '志愿活动几点集合？',
  '期末考试是什么时候？',
  '讲座在哪里办，要不要带校园卡？',
  '集合地点有变化吗？',
];

export async function render(root, ctx) {
  const api = ctx.api;
  const engine = api.local;
  const local = api.isLocal;

  const cov = await api.coverage().catch(() => null);
  const stats = engine
    ? engine.stats()
    : { count: cov ? cov.imported_count : 0, groups: ((cov && cov.groups) || []).length, from: '', to: '' };
  const empty = !stats.count;

  root.innerHTML = `
    <div class="wb-hero">
      <div>
        <h2>把群通知粘进来，直接问就行</h2>
        <p>${local
          ? '数据只存在这台设备的浏览器里，不上传、不用登录。检索在本地完成，断网也能用。'
          : '当前连着成员 D 的后端，导入与检索走服务端接口。'}</p>
      </div>
      <div class="wb-stat">
        <div><strong>${stats.count}</strong><span>条原文</span></div>
        <div><strong>${stats.groups}</strong><span>个来源</span></div>
        <div><strong>${stats.from && stats.to ? esc(stats.from.slice(5)) + ' ~ ' + esc(stats.to.slice(5)) : '—'}</strong><span>时间跨度</span></div>
      </div>
    </div>

    <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(340px,1fr));gap:16px;align-items:start">
      <!-- ================= 输入 ================= -->
      <section class="card">
        <div class="card__head">
          <h2>1 · 导入群通知</h2>
          <span class="spacer"></span>
          ${local ? '<button class="btn btn--sm btn--ghost" data-act="clear">清空全部</button>' : ''}
        </div>
        <div class="card__body">
          <textarea class="textarea" id="wb-text" rows="${empty ? 12 : 7}"
            placeholder="把群里的通知原文粘到这里。&#10;&#10;多条之间用空行分隔；也可以在每条开头写 [群名]，会自动归到那个群。&#10;例：&#10;[2024级计科1班群]&#10;本周六上午8:30在图书馆东门集合…"></textarea>

          <div class="row" style="margin-top:10px;gap:8px;flex-wrap:wrap">
            <input class="input" id="wb-group" placeholder="群标签（可选，未填则用 [群名] 或「未标注群」）" style="flex:1;min-width:180px" />
            <button class="btn btn--primary" id="wb-import">导入并建索引</button>
          </div>

          <div class="row" style="margin-top:8px;gap:8px;flex-wrap:wrap">
            <label class="btn btn--sm btn--ghost" style="cursor:pointer">
              从文件导入 .txt / .json<input type="file" id="wb-file" accept=".txt,.md,.json,.csv" hidden />
            </label>
            ${empty ? '<button class="btn btn--sm btn--ghost" data-act="sample">没有现成数据？载入 4 条示例</button>' : ''}
            <span class="spacer"></span>
            <span class="small muted" id="wb-count">0 字</span>
          </div>

          <div id="wb-import-out" style="margin-top:10px"></div>
        </div>
      </section>

      <!-- ================= 输出 ================= -->
      <section class="card">
        <div class="card__head">
          <h2>2 · 提问</h2>
          <span class="spacer"></span>
          <span class="badge ${local ? 'badge--ok' : 'badge--info'}">${local ? '本地检索' : '后端检索'}</span>
        </div>
        <div class="card__body">
          <div class="row" style="gap:8px">
            <input class="input" id="wb-q" placeholder="例如：志愿活动几点集合？" style="flex:1" />
            <button class="btn btn--primary" id="wb-go">查一下</button>
          </div>
          <div class="chips" style="margin-top:10px">
            ${EXAMPLES.map((q) => `<button class="chip" data-example="${esc(q)}" style="cursor:pointer">${esc(q)}</button>`).join('')}
          </div>
          <div id="wb-answer" style="margin-top:12px">
            ${empty
              ? '<div class="notice notice--muted"><span class="notice__icon">i</span><div>左边还没有数据。先粘贴一段群通知，这里就能出答案。</div></div>'
              : '<div class="small muted">输入问题，或点上面的例子。</div>'}
          </div>
        </div>
      </section>
    </div>

    <!-- ================= 最近导入 ================= -->
    <section class="card" style="margin-top:16px">
      <div class="card__head">
        <h2>已导入的原文</h2>
        <span class="spacer"></span>
        <button class="btn btn--sm btn--ghost" data-act="export">导出全部 JSON</button>
        <button class="btn btn--sm btn--ghost" data-act="open-library">去消息库管理</button>
      </div>
      <div class="card__body card__body--tight" id="wb-list">
        <div class="small muted" style="padding:10px">载入中…</div>
      </div>
    </section>
  `;

  const textEl = root.querySelector('#wb-text');
  const groupEl = root.querySelector('#wb-group');
  const countEl = root.querySelector('#wb-count');
  const outEl = root.querySelector('#wb-import-out');
  const answerEl = root.querySelector('#wb-answer');

  textEl.addEventListener('input', () => {
    const segs = textEl.value.split(/\n\s*\n/).filter((s) => s.trim()).length;
    countEl.textContent = `${textEl.value.length} 字 · ${segs} 段`;
  });

  /* ------------------------------------------------------------ 导入 */
  async function doImport(items, note) {
    if (!items.length) {
      outEl.innerHTML = `<div class="notice notice--warn"><span class="notice__icon">!</span><div>没解析出内容，至少要有 4 个字的一段文字。</div></div>`;
      return;
    }
    const payload = {
      idempotency_key: 'wb-' + Date.now().toString(36),
      messages: items.slice(0, 200).map((x) => ({
        content: x.content,
        group_label: x.group_label || '',
        sent_at: x.sent_at || null,
        metadata: x.metadata || {},
      })),
    };
    try {
      const res = await api.importMessages(payload);
      outEl.innerHTML = `<div class="notice ${res.failure_count ? 'notice--warn' : 'notice--info'}">
        <span class="notice__icon">${res.failure_count ? '!' : '✓'}</span>
        <div>写入 <strong>${res.success_count}</strong> 条${res.failure_count ? `，失败 <strong>${res.failure_count}</strong> 条` : ''}${note ? ' · ' + esc(note) : ''}
        ${res.errors && res.errors.length ? `<div class="small" style="margin-top:4px">${res.errors.map((e) => `第 ${e.index + 1} 条：${esc(e.message)}`).join('<br>')}</div>` : ''}
        </div></div>`;
      if (res.success_count) {
        textEl.value = '';
        countEl.textContent = '0 字';
        toast(`已导入 ${res.success_count} 条`, 'ok');
        await refreshAll();
      }
    } catch (err) {
      outEl.innerHTML = `<div class="notice notice--danger"><span class="notice__icon">✕</span><div>${esc(err.message || String(err))}</div></div>`;
    }
  }

  function collectFromText() {
    const raw = textEl.value.trim();
    if (!raw) return [];
    const group = groupEl.value.trim();

    if (engine) {
      const parsed = engine.parseBulk(raw);
      return parsed.items.map((x) => ({ ...x, group_label: x.group_label || group }));
    }

    // 后端模式：自己按空行切段（后端不做文本解析）
    return raw
      .split(/\n\s*\n/)
      .map((b) => b.trim())
      .filter(Boolean)
      .map((block) => ({ content: block, group_label: group, sent_at: null }));
  }

  root.querySelector('#wb-import').onclick = () => doImport(collectFromText());

  root.querySelector('#wb-file').onchange = async (e) => {
    const file = e.target.files && e.target.files[0];
    if (!file) return;
    if (file.size > 1024 * 1024) {
      outEl.innerHTML = `<div class="notice notice--warn"><span class="notice__icon">!</span><div>文件超过 1 MiB，拆小一点再导。</div></div>`;
      return;
    }
    const text = await file.text();
    const fallbackGroup = groupEl.value.trim() || file.name.replace(/\.[^.]+$/, '');
    if (engine) {
      const parsed = engine.parseBulk(text);
      doImport(parsed.items.map((x) => ({ ...x, group_label: x.group_label || fallbackGroup })), `来自 ${file.name}`);
    } else {
      const items = text
        .split(/\n\s*\n/)
        .map((b) => b.trim())
        .filter(Boolean)
        .map((c) => ({ content: c, group_label: fallbackGroup }));
      doImport(items, `来自 ${file.name}`);
    }
    e.target.value = '';
  };

  const sampleBtn = root.querySelector('[data-act="sample"]');
  if (sampleBtn) {
    sampleBtn.onclick = async () => {
      if (engine) {
        const res = await engine.loadSample();
        outEl.innerHTML = `<div class="notice notice--info"><span class="notice__icon">✓</span><div>已载入 ${res.success_count} 条示例校园通知，可以直接在右边提问。不想要了点「清空全部」。</div></div>`;
        await refreshAll();
      } else {
        const { SAMPLE_MESSAGES } = await import('../local-engine.js');
        doImport(
          SAMPLE_MESSAGES.map((m) => ({ content: m.content, group_label: m.group_label, sent_at: m.sent_at, metadata: m.metadata })),
          '示例数据',
        );
      }
    };
  }

  const clearBtn = root.querySelector('[data-act="clear"]');
  if (clearBtn) {
    clearBtn.onclick = async () => {
      const ok = await confirmDialog({
        title: '清空本地数据',
        message: '会删除这台设备上已导入的全部原文和会话记录，无法撤销。确定吗？',
        confirmText: '清空',
        danger: true,
      });
      if (!ok) return;
      await engine.clearAll();
      ctx.clearSessions();
      toast('已清空', 'ok');
      render(root, ctx);
    };
  }

  root.querySelector('[data-act="export"]').onclick = async () => {
    const page = await api.listMessages({ limit: 2000, offset: 0 });
    const items = page.items || [];
    if (!items.length) return toast('还没有数据可导出', 'warn');
    downloadJson(`qunyi-export-${new Date().toISOString().slice(0, 10)}.json`, {
      exported_at: new Date().toISOString(),
      mode: api.isLocal ? 'local' : 'backend',
      count: items.length,
      messages: items,
    });
    toast(`已导出 ${items.length} 条`, 'ok');
  };

  root.querySelector('[data-act="open-library"]').onclick = () => ctx.navigate('#/library');

  /* ------------------------------------------------------------ 提问 */
  const qEl = root.querySelector('#wb-q');
  async function ask(extra = {}) {
    const q = qEl.value.trim();
    if (!q) return toast('先输入一个问题', 'warn');
    // 动态取当前条数，避免“先导入再提问”时用的是页面加载时的旧值
    const curCount = engine ? engine.stats().count : ctx.state.coverage ? ctx.state.coverage.imported_count : 0;
    if (!curCount) return toast('左边还没有数据，先导入通知', 'warn');

    answerEl.innerHTML = `<div class="notice notice--muted"><span class="notice__icon">◌</span><div>正在检索原文…</div></div>`;
    try {
      const r = await api.query({ question: q, search_mode: 'expanded', ...extra });
      answerEl.innerHTML = renderAnswer(r);
      bindAnswer(answerEl, ctx, r, ask, qEl);
    } catch (err) {
      answerEl.innerHTML = `<div class="notice notice--danger"><span class="notice__icon">✕</span><div>${esc(err.message || String(err))}</div></div>`;
    }
  }

  root.querySelector('#wb-go').onclick = ask;
  qEl.addEventListener('keydown', (e) => {
    if (e.key === 'Enter') {
      e.preventDefault();
      ask();
    }
  });
  root.querySelectorAll('[data-example]').forEach((b) => {
    b.onclick = () => {
      qEl.value = b.dataset.example;
      ask();
    };
  });

  /* -------------------------------------------------------- 刷新列表 */
  async function refreshAll() {
    const page = await api.listMessages({ limit: 30, offset: 0 });
    const items = page.items || [];
    const cov2 = await api.coverage().catch(() => null);
    if (cov2) ctx.state.coverage = cov2;

    const listEl = root.querySelector('#wb-list');
    if (!items.length) {
      listEl.innerHTML = '<div class="small muted" style="padding:10px">还没有导入任何原文。</div>';
    } else {
      listEl.innerHTML = items
        .map(
          (m) => `<button class="nav__item" data-msg="${esc(m.message_id)}" style="align-items:flex-start;padding:10px 12px">
            <span style="flex:1;text-align:left;min-width:0">
              <span class="small muted">${esc(m.group_label || '未标注群')}${m.sent_at ? ' · ' + esc(fmtTime(m.sent_at)) : ''}</span>
              <span style="display:block;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;font-size:13px">${esc(m.content.slice(0, 90))}</span>
            </span>
            ${m.metadata && m.metadata.event_key ? `<span class="badge">${esc(m.metadata.event_key)}</span>` : ''}
          </button>`,
        )
        .join('');
      listEl.querySelectorAll('[data-msg]').forEach((b) => {
        b.onclick = () => ctx.openDetail(b.dataset.msg);
      });
    }

    const heroStats = root.querySelectorAll('.wb-stat strong');
    const s = engine
      ? engine.stats()
      : { count: items.length, groups: new Set(items.map((i) => i.group_label)).size, from: '', to: '' };
    if (heroStats.length === 3) {
      heroStats[0].textContent = String(s.count);
      heroStats[1].textContent = String(s.groups);
      heroStats[2].textContent = s.from && s.to ? s.from.slice(5) + ' ~ ' + s.to.slice(5) : '—';
    }
    ctx.refreshShell();
  }

  await refreshAll();
  if (empty) textEl.focus();
  else qEl.focus();
}

/* ------------------------------------------------------------- 结果卡 */
function toneOf(status) {
  return { answer: 'ok', clarify: 'info', not_found: 'muted', uncertain: 'warn', error: 'danger' }[status] || 'muted';
}
function textOf(status) {
  return { answer: '找到答案', clarify: '需要你确认', not_found: '没有找到', uncertain: '证据不足', error: '出错了' }[status] || status;
}

function renderAnswer(r) {
  const status = r.status || 'uncertain';
  let body = '';

  if (status === 'answer') {
    body = `
      <div class="answer-summary">${esc(r.answer || '').replace(/\n/g, '<br />')}</div>
      ${(r.fields || []).length
        ? `<div class="fields" style="margin-top:10px">${r.fields
            .map(
              (f) => `<div class="field-item">
                <div class="field-item__name">${esc(f.name)}</div>
                <div>
                  <div class="field-item__value">${esc(f.value)}</div>
                  <div class="field-item__quote">${esc(f.quote)}</div>
                  <button class="btn btn--sm btn--ghost" style="margin-top:4px" data-open="${esc(f.message_id)}">看原文 →</button>
                </div>
              </div>`,
            )
            .join('')}</div>`
        : ''}
      ${renderCites(r.citations)}`;
  } else if (status === 'clarify') {
    body = `
      <div class="answer-summary">${esc(r.question || '你想问的是哪一个？')}</div>
      <div class="options" style="margin-top:8px">${(r.options || [])
        .map(
          (o) =>
            `<button class="option-btn" data-opt="${esc(o.event_key)}" data-opt-title="${esc(o.title)}">${esc(o.title)}</button>`,
        )
        .join('')}</div>
      <div class="small muted" style="margin-top:6px">点一个，就用它作为筛选条件继续问。</div>`;
  } else if (status === 'not_found') {
    body = `<div class="answer-summary">${esc(r.answer || '没有找到相关原文。')}</div>
      ${(r.suggestions || []).length
        ? `<div style="margin-top:10px"><div class="small muted" style="margin-bottom:6px">库里这几条可能沾边，点开看看：</div>
            ${r.suggestions
              .map(
                (s) => `<button class="nav__item" data-open="${esc(s.message_id)}" style="align-items:flex-start;padding:8px 10px">
                  <span style="flex:1;text-align:left;min-width:0">
                    <span class="small muted">${esc(s.group_label || '')}</span>
                    <span style="display:block;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;font-size:13px">${esc(s.snippet || '')}</span>
                  </span>
                </button>`,
              )
              .join('')}
          </div>`
        : ''}`;
  } else {
    body = `<div class="answer-summary">${esc((r.error && r.error.message) || r.answer || '检索失败')}</div>`;
  }

  return `
    <div class="answer-card">
      <div class="answer-card__head">
        <span class="badge badge--${toneOf(status)}">${esc(textOf(status))}</span>
        <span class="spacer"></span>
        <button class="btn btn--sm btn--ghost" data-act="copy-md">复制答案</button>
      </div>
      <div class="answer-card__body">${body}</div>
    </div>`;
}

function renderCites(cites) {
  if (!cites || !cites.length) return '';
  return `
    <div style="margin-top:12px">
      <div class="small muted" style="margin-bottom:6px">原文引用（${cites.length} 条，可逐字核对）</div>
      ${cites
        .map(
          (c) => `<div class="cite" style="display:block;padding:8px 10px;border-left:3px solid var(--line);margin-bottom:6px">
            <div class="small muted">${esc(c.group_label || '')}${c.sent_at ? ' · ' + esc(fmtTime(c.sent_at)) : ''}</div>
            <div>${esc(c.quote || '')}</div>
            <button class="btn btn--sm btn--ghost" style="margin-top:4px" data-open="${esc(c.message_id)}">打开原文</button>
          </div>`,
        )
        .join('')}
    </div>`;
}

function bindAnswer(host, ctx, r, ask, qEl) {
  host.querySelectorAll('[data-open]').forEach((b) => {
    b.onclick = () => ctx.openDetail(b.dataset.open);
  });
  // 澄清选项：点了就带着 event_key 继续追问
  host.querySelectorAll('[data-opt]').forEach((b) => {
    b.onclick = () => {
      if (qEl) qEl.value = `「${b.dataset.optTitle}」具体安排是什么`;
      if (typeof ask === 'function') ask({ condition_updates: { event_key: b.dataset.opt } });
    };
  });
  const copy = host.querySelector('[data-act="copy-md"]');
  if (copy) {
    copy.onclick = () => {
      const lines = [r.answer || ''];
      (r.fields || []).forEach((f) => lines.push(`- ${f.name}：${f.value}`));
      if (navigator.clipboard) navigator.clipboard.writeText(lines.join('\n'));
      toast('已复制', 'ok');
    };
  }
}
