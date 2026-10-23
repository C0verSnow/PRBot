import { views } from '../types';
import type { View } from '../types';
import { Icon } from './Icon';
interface Props { active: View; onSelect: (view: View) => void; onSettings: () => void; user: string }
export function Sidebar({ active, onSelect, onSettings, user }: Props) {
  return <aside aria-label="Task navigation" className="flex w-[252px] shrink-0 flex-col border-r border-line bg-sidebar px-3 pt-5 max-[940px]:w-[190px]">
    <div className="flex items-center gap-3 px-3 pb-8 font-serif text-[27px]"><Icon name="brand" className="size-7 text-accent" />PRBot</div>
    <nav className="flex-1">{views.map(view => <button key={view} data-view={view}
      aria-current={active === view ? 'page' : undefined} onClick={() => onSelect(view)}
      className={`nav-item mb-1 ${active === view ? 'bg-[#eae8e2] text-ink' : ''}`}>
      <Icon name={view === 'Issue' ? 'issue' : view === 'Pull Request' ? 'pull' : 'chat'} /><span>{view}</span></button>)}</nav>
    <button id="user" onClick={onSettings} aria-haspopup="dialog" className="flex min-h-[64px] items-center gap-3 border-t border-line px-3 text-left">
      <span className="grid size-8 place-items-center rounded-full bg-[#e5e3dc]">{user[0]?.toUpperCase()}</span>
      <span className="min-w-0 flex-1 truncate">{user}</span><span className="text-xs text-muted">Settings</span>
    </button>
  </aside>;
}
