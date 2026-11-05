from pathlib import Path
import sys
p=Path(sys.argv[1]);s=p.read_text(); original=s
marker='function render(){'
helper='''const SEARCH_HISTORY_KEY='qunyi-search-history-v1';
function readSearchHistory(){if(!workspaceKey)return [];try{const v=JSON.parse(localStorage.getItem(SEARCH_HISTORY_KEY+'-'+workspaceKey)||'[]');return Array.isArray(v)?v.filter(x=>x&&typeof x.q==='string'&&typeof x.at==='number').slice(0,30):[]}catch(_){return []}}
function drawSearchHistory(){const list=view.querySelector('#search-history-list');if(!list)return;const history=readSearchHistory();list.innerHTML=history.length?history.map((x,i)=>`<button type="button" class="history-entry" data-history-index="${i}"><span>${esc(x.q)}</span><time>${esc(new Date(x.at).toLocaleString('zh-CN',{month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit'}))}</time></button>`).join(''):'<div class="history-empty">暂无查询记录</div>';list.querySelectorAll('[data-history-index]').forEach(b=>b.onclick=()=>{const entry=readSearchHistory()[Number(b.dataset.historyIndex)];if(entry){view.querySelector('#q').value=entry.q;search()}});const clear=view.querySelector('#clear-search-history');if(clear){clear.disabled=!history.length;clear.onclick=()=>{if(!workspaceKey)return;try{localStorage.removeItem(SEARCH_HISTORY_KEY+'-'+workspaceKey)}catch(_){}drawSearchHistory()}}}
function rememberSearch(q){if(!workspaceKey)return;const history=readSearchHistory().filter(x=>x.q!==q);history.unshift({q,at:Date.now()});try{localStorage.setItem(SEARCH_HISTORY_KEY+'-'+workspaceKey,JSON.stringify(history.slice(0,30)))}catch(_){}drawSearchHistory()}
'''
assert s.count(marker)==1
s=s.replace(marker,helper+marker,1)
start=s.index('<div class="panel-head" style="margin-top:22px;margin-bottom:7px"><div><h3>消息</h3>',s.index("if(page==='search')"))
end=s.index('</aside></div>`;if(page===\'messages\')',start)
s=s[:start]+'''</div></section><aside class="panel search-latest search-history"><div class="panel-head"><div><h3>查询历史</h3><span class="subtle">点击记录重新查找</span></div><button type="button" class="link" id="clear-search-history">清空</button></div><div id="search-history-list"></div></aside></div>`;'''+s[end+len('</aside></div>`;'):]
needle="view.querySelectorAll('[data-query]').forEach"
assert s.count(needle)==1
s=s.replace(needle,"drawSearchHistory();"+needle,1)
needle="const r=await api('/v1/query','POST',body);"
assert s.count(needle)==1
s=s.replace(needle,needle+"if(!eventKey)rememberSearch(q);",1)
assert '本页消息</h3>' not in s[s.index("if(page==='search')"):s.index("if(page==='messages')")]
p.write_text(s)
print('updated',p)
