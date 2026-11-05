from pathlib import Path
import sys
p=Path(sys.argv[1]);s=p.read_text()
# Explain the two label dimensions and expose them at the point of use.
for a,b in [('查看范围</h3>','查看哪些群</h3>'),('scope-title">群标签 ','scope-title">按群标签查看 '),('scope-title">来源群','scope-title">按单个群查看'),('<strong>消息标签</strong>','<strong>筛选消息标签</strong>')]:
 assert a in s,a;s=s.replace(a,b,1)
s=s.replace('删除标签“${label.name}”？消息不会被删除。','删除消息标签“${label.name}”？它关联了 ${Object.values(tagState.assignments).filter(ids=>ids.includes(id)).length} 条消息。消息不会被删除。',1)
s=s.replace('删除群标签“${t.name}”？群和消息不会被删除。','删除群标签“${t.name}”？它关联了 ${Object.values(groupTagState.assignments).filter(ids=>ids.includes(t.id)).length} 个群。群和消息不会被删除。',1)
# Search citations can be outside the currently loaded page; fetch an authorized message first.
s=s.replace('function showTagPicker(mid,draft=null){let m=data.find(x=>x.id===mid);','let searchTagMessages={};async function openSearchTag(mid){try{const m=await api(\'/v1/messages/\'+encodeURIComponent(mid));searchTagMessages[mid]={id:mid,title:m.metadata?.title||String(m.content||\'群消息\').slice(0,28)};showTagPicker(mid)}catch(e){alert(\'无法读取消息：\'+e.message)}}function showTagPicker(mid,draft=null){let m=data.find(x=>x.id===mid)||searchTagMessages[mid];',1)
needle='<div class="tag-note">标签只保存在当前浏览器，不会同步到其他设备。</div><div class="tag-actions">'
replacement='<div class="tag-note">标签只保存在当前浏览器，不会同步到其他设备。</div><div class="ai-tag-section"><button type="button" class="secondary" id="ai-suggest-tags">AI 建议标签</button><p class="tag-note">点击后将该条消息发送给 AI 分析；选择建议并保存后才添加。</p><div id="ai-tag-results" class="tag-selection" aria-live="polite"></div></div><div class="tag-actions">'
assert needle in s;s=s.replace(needle,replacement,1)
needle="let box=document.querySelector('#tagModalContent');box.querySelector('#picker-manage').onclick"
replace="""let box=document.querySelector('#tagModalContent');box.querySelector('#ai-suggest-tags').onclick=async()=>{const btn=box.querySelector('#ai-suggest-tags'),out=box.querySelector('#ai-tag-results');btn.disabled=true;out.textContent='AI 正在分析…';try{const r=await api('/v1/tag-suggestions','POST',{message_id:mid});out.innerHTML=r.tags.length?r.tags.map(name=>`<label><input type="checkbox" data-ai-tag="${esc(name)}">${esc(name)}</label>`).join(''):'没有合适的建议，可手动创建。'}catch(e){out.textContent='AI 建议失败：'+e.message}finally{btn.disabled=false}};box.querySelector('#picker-manage').onclick"""
assert needle in s;s=s.replace(needle,replace,1)
s=s.replace("box.querySelectorAll('input:checked')","box.querySelectorAll('.tag-selection:not(#ai-tag-results) input:checked')")
needle="tagState.assignments[mid]=Array.from(box.querySelectorAll('.tag-selection:not(#ai-tag-results) input:checked')).map(x=>x.value);saveTags();"
replace="""const ids=Array.from(box.querySelectorAll('.tag-selection:not(#ai-tag-results) input:checked')).map(x=>x.value);box.querySelectorAll('#ai-tag-results input:checked').forEach(x=>{const name=x.dataset.aiTag;let t=tagState.labels.find(t=>t.name.normalize('NFKC').toLocaleLowerCase()===name.normalize('NFKC').toLocaleLowerCase());if(!t){t={id:'local-'+Date.now()+'-'+Math.random().toString(36).slice(2,8),name,color:'#0A84FF'};tagState.labels.push(t)}if(!ids.includes(t.id))ids.push(t.id)});tagState.assignments[mid]=ids;saveTags();"""
assert needle in s;s=s.replace(needle,replace,1)
# Preserve visible search results after a label assignment by not rendering the whole page.
s=s.replace('saveTags();closeTagDialog();render()}}\nfunction showGroupTagManager','saveTags();closeTagDialog();if(page!==\'search\')render()}}\nfunction showGroupTagManager',1)
needle="host.querySelectorAll('[data-citation]').forEach(b=>b.onclick=()=>openMsg(b.dataset.citation))"
assert needle in s;s=s.replace(needle,needle+";host.querySelectorAll('[data-tag-citation]').forEach(b=>b.onclick=()=>openSearchTag(b.dataset.tagCitation))",1)
p.write_text(s)
print('UI updated')
