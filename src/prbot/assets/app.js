'use strict';
const $ = id => document.getElementById(id);
const descriptions = {
  Projects: '当前版本尚未提供项目管理。请在 New 中整理仓库需求；允许操作的仓库范围在用户设置中配置。',
  Artifacts: '当前版本尚未提供独立产物库。代码变更和交付结果请查看 Telegram 回执中的 GitHub PR。',
  Code: '仓库修改由 Telegram /FinishIssue 启动。当前窗口不提供代码编辑器，也不在本地编译代码。',
  Pinned: '当前版本尚未提供固定项目功能。你可以从 New 整理新的仓库需求。',
  'Chats and tasks': '当前窗口尚未同步 Telegram 会话和任务列表。请到 Bot 会话查看任务编号、执行状态及 PR 交付回执。'
};
function selectView(view) {
  document.querySelectorAll('[data-view]').forEach(button => {
    const active = button.dataset.view === view;
    button.classList.toggle('active', active);
    if (active) button.setAttribute('aria-current', 'page');
    else button.removeAttribute('aria-current');
  });
  $('new-view').hidden = view !== 'New';
  $('empty-view').hidden = view === 'New';
  $('empty-title').textContent = view;
  $('empty-description').textContent = descriptions[view] || '';
}
function send(action, text) {
  if (!window.webkit?.messageHandlers?.prbot) {
    receiveNative({ok: false, message: '请在 PRBot macOS 应用中使用此功能。'});
    return;
  }
  document.querySelectorAll('[data-action], #handoff').forEach(b => b.disabled = true);
  if (action !== 'ready') $('settings-feedback').textContent = '正在处理，请完成系统对话框中的操作…';
  window.webkit.messageHandlers.prbot.postMessage({action, text});
}
window.receiveNative = payload => {
  document.querySelectorAll('[data-action], #handoff').forEach(b => b.disabled = !!payload.busy);
  if (payload.message) for (const id of ['feedback', 'settings-feedback']) {
    $(id).textContent = payload.message;
    $(id).classList.toggle('error', !payload.ok);
  }
  if (payload.status) {
    const s = payload.status;
    const state = s.state || 'unknown';
    const labels = {running: '运行中', starting: '启动检查中', halted: '启动检查未通过', stopped: '已停止', crashed: '服务异常', unknown: '尚无状态', unavailable: '无法读取状态'};
    const label = s.loaded ? (labels[state] || '未知状态') : (s.loaded === false ? '未加载服务' : labels[state]);
    $('service-label').textContent = label;
    $('status-dot').classList.toggle('running', s.loaded && state === 'running');
    $('service-detail').textContent = `${label} · launchd ${s.loaded ? '已加载' : '未加载'} · 最近报告：${labels[state] || '未知状态'}`;
    $('autostart').textContent = s.autostart ? '当前已开启登录自启动。' : '当前未开启登录自启动。';
    if (s.workspace) $('workspace').textContent = s.workspace;
    if (s.user) {
      $('username').textContent = s.user;
      $('avatar').textContent = Array.from(s.user)[0].toUpperCase();
    }
  }
};
document.querySelectorAll('[data-view]').forEach(b => b.addEventListener('click', () => selectView(b.dataset.view)));
document.querySelectorAll('[data-action]').forEach(b => b.addEventListener('click', () => send(b.dataset.action)));
$('back-new').addEventListener('click', () => selectView('New'));
for (const id of ['user', 'status-button']) $(id).addEventListener('click', () => { $('settings').showModal(); send('ready'); });
$('close-settings').addEventListener('click', () => $('settings').close());
$('composer').addEventListener('submit', event => { event.preventDefault(); send('draft', $('draft').value); });
// Refresh only status while idle; never interrupt an active native dialog.
setInterval(() => { if (!$('handoff').disabled) send('ready'); }, 15000);
send('ready');
