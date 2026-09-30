const $ = (id) => document.getElementById(id);
const token = document.querySelector('meta[name="studio-token"]').content;
let state, dirty = false, pendingState = null, currentPlatform = null, saving = false, connectionSaving = false;
let toastTimer;

async function api(path, body) {
  let response;
  try {
    response = await fetch(path, {method: body === undefined ? 'GET' : 'POST',
      headers: {'X-Studio-Token': token, ...(body === undefined ? {} : {'Content-Type': 'application/json'})},
      ...(body === undefined ? {} : {body: JSON.stringify(body)})});
  } catch {
    throw new Error('暂时连不上本地服务，输入内容仍保留。');
  }
  const result = await response.json();
  if (!response.ok) {const error = new Error(result.message || '操作未完成，请重试。'); error.status = response.status; throw error;}
  return result;
}

function toast(message) {
  clearTimeout(toastTimer);
  $('toast').textContent = message;
  $('toast').hidden = false;
  toastTimer = setTimeout(() => {$('toast').hidden = true;}, 3800);
}

function updateProfileState() {
  const pill = $('profile-state');
  pill.textContent = dirty ? '未保存' : state?.ready ? '已保存' : '待填写';
  pill.className = 'status-pill' + (dirty ? ' dirty' : state?.ready ? ' ready' : '');
  $('save-profile').disabled = saving || !state || !dirty || !$('user-info').value.trim() || !!pendingState;
  $('nav-dot').classList.toggle('ready', !!state?.ready);
}

function render(data, replaceProfile = false) {
  state = data;
  if (replaceProfile) {
    $('user-info').value = data.profile.user_info;
    dirty = false;
    pendingState = null;
    $('conflict').hidden = true;
    $('profile-error').hidden = true;
  }
  updateProfileState();
  for (const platform of ['bilibili', 'zhihu']) {
    const configured = data.connections[platform].configured;
    $(platform + '-status').textContent = configured ? '已配置' : '未配置';
    $(platform + '-status').classList.toggle('configured', configured);
    $(platform + '-status').title = configured ? '已有本地凭据，登录有效性将在使用时检查' : '可稍后配置';
    $(platform + '-action').textContent = configured ? '管理连接' : '连接';
  }
  $('examples-status').textContent = data.examples.count ? `${data.examples.count} 个文件` : '待放入';
  $('examples-status').classList.toggle('configured', data.examples.count > 0);
  $('folder-path').textContent = data.examples.path;
  $('example-files').replaceChildren(...data.examples.files.map(name => {
    const li = document.createElement('li'); li.textContent = name; return li;
  }));
}

async function refresh() {
  if (saving) return;
  try {
    const latest = await api('/api/state');
    if (dirty) {
      if (state && latest.revision !== state.revision) {
        pendingState = latest;
        $('conflict').hidden = false;
        updateProfileState();
      }
      // Do not replace the revision associated with an unsaved local draft.
      return;
    }
    render(latest, true);
  } catch (error) {
    $('profile-error').textContent = error.message;
    $('profile-error').hidden = false;
  }
}

$('user-info').addEventListener('input', () => {
  dirty = $('user-info').value !== (state?.profile.user_info || '');
  $('save-message').textContent = dirty ? '仅保存在本机' : '已与本地文件同步';
  updateProfileState();
});
$('profile-form').addEventListener('submit', async event => {
  event.preventDefault();
  if ($('save-profile').disabled) return;
  const draft = $('user-info').value;
  saving = true; updateProfileState(); $('save-profile').querySelector('span').textContent = '保存中';
  try {
    const latest = await api('/api/profile', {user_info: draft, revision: state.revision});
    if ($('user-info').value === draft) render(latest, true);
    else {state = latest; dirty = true;}
    $('save-message').textContent = '已保存到本机';
    $('profile-error').hidden = true;
    toast('已记住你的用户信息');
  } catch (error) {
    $('profile-error').textContent = error.message; $('profile-error').hidden = false;
    if (error.status === 409) {
      try {pendingState = await api('/api/state'); $('conflict').hidden = false;} catch {}
    }
  } finally {
    saving = false; $('save-profile').querySelector('span').textContent = '保存'; updateProfileState();
  }
});
$('load-latest').addEventListener('click', () => {
  if (!pendingState) return;
  if (!window.confirm('载入最新内容会替换当前未保存的草稿。继续吗？')) return;
  render(pendingState, true);
});
window.addEventListener('focus', refresh);
document.addEventListener('visibilitychange', () => {if (!document.hidden) refresh();});
window.addEventListener('beforeunload', event => {
  if (dirty) {event.preventDefault(); event.returnValue = '';}
});

function openConnection(platform) {
  if (!state) return toast('配置还在读取，请稍候');
  currentPlatform = platform;
  const bili = platform === 'bilibili';
  const name = bili ? 'B 站' : '知乎';
  const domain = bili ? 'www.bilibili.com' : 'www.zhihu.com';
  const fields = bili ? ['SESSDATA'] : ['z_c0', 'd_c0', '_xsrf'];
  $('connection-title').textContent = `连接${name}`;
  const connection = state.connections[platform];
  $('connection-state').textContent = connection.managed_by_environment ? '由环境配置提供' : connection.configured ? '已配置 · 可在此更新' : '未配置';
  $('credential-fields').replaceChildren(...fields.map(field => {
    const label = document.createElement('label'); label.className = 'field';
    const top = document.createElement('span'); top.className = 'field-label';
    const text = document.createElement('span'); text.textContent = field; top.append(text);
    if (field === '_xsrf') {const optional = document.createElement('small'); optional.textContent = '选填'; top.append(optional);}
    const input = document.createElement('input'); input.type = 'password'; input.name = field;
    input.required = field !== '_xsrf'; input.placeholder = connection.configured ? '粘贴新值以更新' : '粘贴完整值';
    input.autocomplete = 'off'; input.spellcheck = false; input.maxLength = 65536;
    input.disabled = connection.managed_by_environment;
    label.append(top, input); return label;
  }));
  $('save-connection').disabled = connection.managed_by_environment || connectionSaving;
  $('platform-link').href = `https://${domain}`; $('platform-link').textContent = `打开${name} ↗`;
  $('sketch-domain').textContent = domain; $('sketch-logo').textContent = name;
  $('tree-domain').textContent = '　　https://' + domain;
  $('cookie-field').textContent = fields[0];
  $('fields-hint').textContent = bili ? '复制 SESSDATA 的值，粘贴到左侧。' : '依次复制 z_c0、d_c0；如有 _xsrf，也可一并填写。';
  $('connection-error').hidden = true;
  $('connection-dialog').showModal();
}
document.querySelectorAll('[data-connect]').forEach(button => button.addEventListener('click', () => openConnection(button.dataset.connect)));
$('connection-form').addEventListener('submit', async event => {
  event.preventDefault();
  const button = $('save-connection'); if (button.disabled) return;
  const platform = currentPlatform;
  connectionSaving = true;
  button.disabled = true;
  const payload = Object.fromEntries(new FormData(event.currentTarget));
  try {
    const data = await api(`/api/connections/${platform}`, payload);
    // Credential updates must not discard a draft being written in the profile form.
    if (!dirty) render(data, true);
    else {
      state.connections = data.connections;
      $(platform + '-status').textContent = '已配置'; $(platform + '-status').classList.add('configured');
      $(platform + '-action').textContent = '管理连接';
    }
    if (currentPlatform === platform) $('connection-dialog').close();
    toast('配置已保存');
  } catch (error) {
    $('connection-error').textContent = error.message; $('connection-error').hidden = false;
  } finally {connectionSaving = false; button.disabled = !!(currentPlatform && state.connections[currentPlatform].managed_by_environment);}
});
document.querySelectorAll('[data-close]').forEach(button => button.addEventListener('click', () => button.closest('dialog').close()));
$('connection-dialog').addEventListener('close', () => {$('connection-form').reset(); $('credential-fields').replaceChildren(); currentPlatform = null;});
document.querySelectorAll('dialog').forEach(dialog => dialog.addEventListener('click', event => {
  const rect = dialog.getBoundingClientRect();
  if (event.target === dialog && (event.clientX < rect.left || event.clientX > rect.right || event.clientY < rect.top || event.clientY > rect.bottom)) dialog.close();
}));
function showExamples() {if (state) $('examples-dialog').showModal(); else toast('配置还在读取，请稍候');}
$('examples-details').addEventListener('click', showExamples);
$('open-examples').addEventListener('click', showExamples);
$('open-folder').addEventListener('click', async () => {
  try {await api('/api/examples/open', {}); toast('文件夹已打开');} catch(error) {toast(error.message);}
});
$('copy-path').addEventListener('click', async () => {
  try {await navigator.clipboard.writeText(state.examples.path); toast('路径已复制');}
  catch {toast('复制未完成，可直接选中路径复制');}
});
document.querySelectorAll('.nav-item').forEach(link => link.addEventListener('click', () => {
  document.querySelectorAll('.nav-item').forEach(item => item.classList.toggle('active', item === link));
}));
refresh();
