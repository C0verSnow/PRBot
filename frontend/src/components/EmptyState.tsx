import type { View } from '../types';
const descriptions: Record<Exclude<View, 'New'>, string> = {
  Projects: '当前版本尚未提供项目管理。请在 New 中整理仓库需求；允许操作的仓库范围在用户设置中配置。',
  Artifacts: '当前版本尚未提供独立产物库。代码变更和交付结果请查看 Telegram 回执中的 GitHub PR。',
  Code: '仓库修改由 Telegram /FinishIssue 启动。当前窗口不提供代码编辑器，也不在本地编译代码。',
  Pinned: '当前版本尚未提供固定项目功能。你可以从 New 整理新的仓库需求。',
  'Chats and tasks': '当前窗口尚未同步 Telegram 会话和任务列表。请到 Bot 会话查看任务编号、执行状态及 PR 交付回执。',
};
export function EmptyState({ view, onNew }: { view: View; onNew: () => void }) {
  return <section id="empty-view" hidden={view === 'New'} className="mx-auto mt-[16vh] max-w-[520px] p-6 text-center">
    <span aria-hidden="true" className="text-[50px] text-accent">◌</span><h1 className="my-[15px] font-serif text-[34px] tracking-[-1px]">{view}</h1><p id="empty-description" className="mb-6 leading-[1.9] text-muted">{view === 'New' ? '' : descriptions[view]}</p><button id="back-new" onClick={onNew} className="secondary">整理新需求</button>
  </section>;
}
