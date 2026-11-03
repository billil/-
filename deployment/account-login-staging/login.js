/* The owner account is provisioned once; there is no public signup. */
(function () {
  const oldKey = 'qunyi.demo.access';
  const rememberedKey = 'qunyi.demo.remembered';
  async function json(url, data) {
    const r = await fetch(url, data ? {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(data)} : {cache:'no-store'});
    const val = await r.json();
    if (!r.ok) throw Error(val.error?.message || val.error || '请求失败');
    return val;
  }
  async function loadApp(username) {
    const previous=sessionStorage.getItem(oldKey)||localStorage.getItem(rememberedKey)||'';
    const next='account:'+username;
    // Keep existing local tags when upgrading the same browser, without sending them away.
    if(previous && !previous.startsWith('account:') && globalThis.crypto?.subtle){
      try {
        const hash=async text=>Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256',new TextEncoder().encode(text)))).map(n=>n.toString(16).padStart(2,'0')).join('');
        const oldHash=await hash(previous), newHash=await hash(next);
        for(const prefix of ['qunyi-demo-labels-v1','qunyi-demo-group-tags-v1']){
          const oldValue=localStorage.getItem(prefix+'-'+oldHash), newKey=prefix+'-'+newHash;
          if(oldValue && !localStorage.getItem(newKey))localStorage.setItem(newKey,oldValue);
        }
      }catch(_){}
    }
    sessionStorage.setItem(oldKey, next);
    localStorage.removeItem(rememberedKey);
    document.getElementById('login-screen')?.remove();
    document.querySelector('.layout').hidden = false;
    let script=document.createElement('script');script.src='/assets/qunyi-account-app.js?v=20261103a';document.body.appendChild(script);
  }
  function loginForm(setup) {
    document.querySelector('.layout').hidden=true;
    const section=document.createElement('main');section.id='login-screen';
    section.innerHTML=`<div class="login-card"><div class="login-brand">群忆 <span>校园消息助手</span></div><h1>${setup?'设置项目账号':'登录群忆'}</h1><p>${setup?'首次设置需要验证当前项目的访问权限。':'登录后可以查看已授权的群消息。'}</p><form id="login-form"><label>账号<input name="username" autocomplete="username" required minlength="3" maxlength="32" placeholder="3–32位字母、数字或下划线"></label><label>密码<input name="password" type="password" autocomplete="${setup?'new-password':'current-password'}" required minlength="12" maxlength="128" placeholder="至少12位"></label>${setup?'<label>原演示访问令牌<input name="access_token" type="password" autocomplete="off" required><small>此项只在首次设置时使用</small></label>':''}<label class="login-remember"><input name="remember" type="checkbox" checked> 在此设备保持登录</label><p id="login-error" role="alert"></p><button class="primary">${setup?'设置并登录':'登录'}</button></form></div>`;
    document.body.appendChild(section);
    const form=section.querySelector('form');
    if(setup){const saved=sessionStorage.getItem(oldKey)||localStorage.getItem(rememberedKey)||'';if(saved&&!saved.startsWith('account:'))form.elements.access_token.value=saved;}
    form.onsubmit=async function(e){e.preventDefault();const button=form.querySelector('button');button.disabled=true;section.querySelector('#login-error').textContent='';try{const result=await json(setup?'/auth/setup':'/auth/login',{username:form.elements.username.value,password:form.elements.password.value,remember:form.elements.remember.checked,...(setup?{access_token:form.elements.access_token.value}:{})});loadApp(form.elements.username.value)}catch(err){section.querySelector('#login-error').textContent=err.message}finally{button.disabled=false}};
  }
  window.qunyiLogout=async function(){try{await json('/auth/logout',{})}finally{sessionStorage.removeItem(oldKey);localStorage.removeItem(rememberedKey);location.reload()}};
  json('/auth/session').then(s=>s.authenticated?loadApp(s.username):loginForm(s.setup_required)).catch(()=>{document.body.textContent='无法连接登录服务，请稍后重试。'});
})();
