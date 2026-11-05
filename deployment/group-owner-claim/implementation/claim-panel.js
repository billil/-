/* Staging UI. The server capability, not the browser, controls availability. */
export async function mountClaimPanel(container) {
  container.replaceChildren();
  const title=document.createElement('h3');title.textContent='接入我的群';
  const info=document.createElement('p');info.textContent='正在检查群管理员身份验证能力…';info.setAttribute('role','status');
  const button=document.createElement('button');button.type='button';button.textContent='开始认领';button.disabled=true;
  container.append(title,info,button);
  try {
    const response=await fetch('/group-access/claim/status',{credentials:'same-origin',cache:'no-store'});
    if(!response.ok)throw new Error('暂时无法检查认领状态');
    const state=await response.json();
    // This build never enables claim: there is no verified backend grant path.
    info.textContent=state.reason||'群管理员认领暂未开放';
  } catch (_) { info.textContent='群管理员认领暂未开放，请稍后再试'; }
}
