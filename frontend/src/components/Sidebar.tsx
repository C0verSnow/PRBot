import { views } from '../types';
import type { View } from '../types';
import { Icon } from './Icon';
import type { IconName } from './Icon';

const icons: Record<View, IconName> = { New: 'plus', Projects: 'project', Artifacts: 'artifact', Code: 'code', Pinned: 'pin', 'Chats and tasks': 'chat' };
interface Props { active: View; onSelect: (view: View) => void; onSettings: () => void; user: string }
export function Sidebar({ active, onSelect, onSettings, user }: Props) {
  return <aside aria-label="主导航" className="flex w-[252px] shrink-0 flex-col border-r border-line bg-sidebar px-2 pt-4 max-[940px]:w-[218px]">
    <div className="flex items-center gap-[15px] px-2.5 pb-[21px] font-serif text-[25px] tracking-[-.8px]"><span aria-hidden="true" className="font-sans text-[17px] text-muted">◫</span>PRBot</div>
    <nav className="flex-1 overflow-y-auto">{views.map((view, index) => <div key={view} className={index > 3 ? 'mt-[29px]' : ''}>
      <button data-view={view} aria-current={active === view ? 'page' : undefined} onClick={() => onSelect(view)} className={`nav-item ${active === view ? 'bg-[#eae8e2] text-ink' : ''} ${index > 3 ? 'justify-between text-xs text-muted' : ''}`}>
        {index < 4 ? <Icon name={icons[view]} /> : null}<span>{view}</span>{index > 3 ? <Icon name={icons[view]} className="size-[13px]" /> : null}
      </button>
      {view === 'Pinned' ? <p className="sidebar-note">尚未固定任何项目</p> : null}
      {view === 'Chats and tasks' ? <p className="sidebar-note">任务回执显示在 Telegram 中</p> : null}
    </div>)}</nav>
    <button id="user" onClick={onSettings} aria-haspopup="dialog" className="mt-3.5 flex min-h-[58px] w-full items-center gap-2 border-t border-line px-1 py-[13px] text-left hover:bg-[#ebe9e3]">
      <span className="grid size-7 shrink-0 place-items-center rounded-full bg-[#e5e3dc] text-xs">{Array.from(user)[0]?.toUpperCase()}</span>
      <span className="max-w-[83px] truncate">{user}</span><span className="text-[11px] text-muted max-[940px]:hidden">设置与服务</span><span className="ml-auto text-muted">⌄</span>
    </button>
  </aside>;
}
