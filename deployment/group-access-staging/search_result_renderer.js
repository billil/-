(function(root){
  'use strict';
  const escape=s=>String(s==null?'':s).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const timeValue=s=>{if(!s)return null;const n=Date.parse(s);return Number.isFinite(n)?n:null};
  const sort=items=>(Array.isArray(items)?items:[]).filter(item=>item&&typeof item==='object').map((item,index)=>({item,index,t:timeValue(item.sent_at)})).sort((a,b)=>a.t===null?(b.t===null?a.index-b.index:1):b.t===null?-1:b.t-a.t||a.index-b.index).map(x=>x.item);
  const dateText=s=>{const t=timeValue(s);return t===null?'时间未知':new Date(t).toLocaleString('zh-CN',{year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',hour12:false})};
  function render(citations,fields,nameFor){
    const list=sort(citations),allFields=Array.isArray(fields)?fields.filter(f=>f&&typeof f==='object'):[];
    const rows=list.map(c=>{
      const group=typeof nameFor==='function'?nameFor(c.group_label):c.group_label;
      const values=allFields.filter(f=>f.message_id===c.message_id).map(f=>`<div class="search-field"><strong>${escape(f.name)}：${escape(f.value)}</strong></div>`).join('');
      return `<article class="quote search-evidence"><div class="search-evidence-time">${escape(dateText(c.sent_at))}</div>${values}<div>${escape(c.quote||'')}</div><div class="search-evidence-actions"><button type="button" class="link" data-citation="${escape(c.message_id)}">查看原文</button><details class="search-source"><summary>查看来源群</summary><span>${escape(group||'未知来源')}</span></details></div></article>`;
    }).join('');
    const unmatched=allFields.filter(f=>!list.some(c=>c.message_id===f.message_id)).map(f=>`<article class="quote"><div class="search-evidence-time">时间未知</div><strong>${escape(f.name)}：${escape(f.value)}</strong><br>${escape(f.quote||'')}</article>`).join('');
    return rows+unmatched;
  }
  function renderOptions(options,nameFor){
    return sort(options).map(o=>{
      const group=typeof nameFor==='function'?nameFor(o.group_label):o.group_label;
      return `<article class="quote search-evidence"><div class="search-evidence-time">${escape(dateText(o.sent_at))}</div><button type="button" class="chip" data-option="${escape(o.event_key)}">${escape(o.title||'查看候选')}</button><div class="search-evidence-actions"><details class="search-source"><summary>查看来源群</summary><span>${escape(group||'未知来源')}</span></details></div></article>`;
    }).join('');
  }
  root.QunyiSearchResults={sort,render,renderOptions,dateText};
})(window);
