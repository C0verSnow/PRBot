import { useEffect, useRef } from 'react';
import type { ReactNode } from 'react';
import type { Action, NativeResult, SendAction } from '../types';

export const stateLabels: Record<string, string> = { running: '运行中', starting: '启动检查中', halted: '启动检查未通过', stopped: '已停止', crashed: '服务异常', unknown: '尚无状态', unavailable: '无法读取状态' };
export function serviceLabel(status: NativeResult['status']) {
  if (!status) return '查看服务状态';
  return status.loaded === false ? '未加载服务' : (stateLabels[status.state] || '未知状态');
}
function SettingGroup({ title, description, help, children }: { title: string; description: string; help: string; children: ReactNode }) {
  return <section className="border-b border-line py-[19px] last:border-0"><h3 className="mb-[9px] text-[13px] font-semibold">{title}</h3><p className="my-2 text-xs leading-[1.8] break-words text-[#77736b]">{description}</p><div className="mt-3 flex flex-wrap gap-2">{children}</div><p className="mt-2 text-[11px] leading-[1.8] text-muted">{help}</p></section>;
}
interface Props { open: boolean; onClose: () => void; busy: boolean; result: NativeResult; send: SendAction }
export function SettingsPanel({ open, onClose, busy, result, send }: Props) {
  const dialog = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    const element = dialog.current;
    if (open && !element?.open) element?.showModal();
    else if (!open && element?.open) element.close();
  }, [open]);
  const actionButton = (action: Action, label: string) => <button key={action} data-action={action} className="secondary" disabled={busy} onClick={() => send(action)}>{label}</button>;
  const status = result.status;
  return <dialog ref={dialog} id="settings" onClose={onClose} aria-labelledby="settings-title" className="settings-dialog">
    <header className="flex items-center justify-between border-b border-line px-7 pt-[25px] pb-[22px]"><div><p className="mb-[9px] text-[10px] tracking-[1.8px] text-muted">YOUR WORKSPACE</p><h2 id="settings-title" className="font-serif text-[28px]">设置与服务</h2></div><button id="close-settings" className="px-[3px] text-[27px] text-muted" aria-label="关闭设置" onClick={onClose}>×</button></header>
    <div className="max-h-[calc(100vh_-_285px)] overflow-auto px-7">
      <SettingGroup title="工作空间" description={status?.workspace || '选择任务的统一工作路径。'} help="首次选择会准备 .env。保存后重启服务；现有任务不搬动。">{actionButton('选择工作路径', '选择工作路径')}{actionButton('打开配置目录', '打开配置目录')}</SettingGroup>
      <SettingGroup title="连接与权限" description="凭据通过系统隐藏输入框填写并保存在钥匙串中，此页面不显示凭据。" help=".env 的配置优先于界面保存值。任务配置包含 GitHub Token、允许用户与仓库；同时导入当前 Codex 登录。">{actionButton('配置 Token', 'Telegram Token')}{actionButton('配置任务', 'GitHub 与允许范围')}{actionButton('导入 Codex 登录', '导入 Codex 登录')}</SettingGroup>
      <SettingGroup title="后台服务" description={`${serviceLabel(status)} · launchd ${status?.loaded ? '已加载' : '未加载'} · 最近报告：${stateLabels[status?.state || 'unknown'] || '未知状态'}`} help="启动操作完成后仍需等待后台检查；停止会移除登录自启动。关闭窗口不停止服务。">{actionButton('启动服务', '启动')}{actionButton('停止服务', '停止')}{actionButton('重启服务', '重启')}{actionButton('查看状态', '查看完整状态')}</SettingGroup>
      <SettingGroup title="登录自启动" description={status?.autostart ? '当前已开启登录自启动。' : '当前未开启登录自启动。'} help="更改此设置会重新注册并启动后台服务。">{actionButton('开启登录自启动', '开启')}{actionButton('关闭登录自启动', '关闭')}</SettingGroup>
    </div>
    <footer id="settings-feedback" role="status" aria-live="polite" className={`border-t border-line px-7 py-4 text-xs leading-[1.7] whitespace-pre-wrap ${result.ok ? 'text-[#6d6a60]' : 'text-[#a74432]'}`}>{result.message || '操作结果将在这里显示。'}</footer>
  </dialog>;
}
