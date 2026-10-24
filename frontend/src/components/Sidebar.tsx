import type { HistorySession, Task, Workspace } from '../types';
import { Icon } from './Icon';
interface Props {
  active: Workspace; onSelect: (view: Workspace) => void; onSettings: () => void; user: string;
  expanded: boolean; onToggle: () => void; sessions: HistorySession[]; selected: string;
  onSession: (id: string) => void; counts: Record<string, number>; loading: boolean; error: string;
  onRefresh: () => void; tasks: Task[];
}
export function Sidebar({ active, onSelect, onSettings, user, expanded, onToggle, sessions, selected, onSession, counts, loading, error, onRefresh, tasks }: Props) {
  return <aside aria-label="Task navigation" className="flex h-screen w-[252px] shrink-0 flex-col border-r border-line bg-sidebar px-3 pt-5 max-[940px]:w-[190px]">
    <div className="flex items-center gap-3 px-3 pb-8 font-serif text-[27px]"><Icon name="brand" className="size-7 text-accent" />PRBot</div>
    <nav className="min-h-0 flex-1 overflow-y-auto">
      {(['GitHub', 'Chat History'] as const).map(view => <button key={view} data-view={view} aria-current={active === view ? 'page' : undefined} onClick={() => onSelect(view)} className={`nav-item mb-1 ${active === view ? 'bg-[#eae8e2] text-ink' : ''}`}><Icon name={view === 'GitHub' ? 'code' : 'plus'} /><span>{view === 'GitHub' ? 'GitHub workspace' : 'Private chat'}</span></button>)}
      <button id="history-toggle" className="nav-item mt-5" aria-expanded={expanded} aria-controls="history-sessions" onClick={onToggle}><Icon name="chat" /><span className="flex-1">Chat History</span><span aria-hidden="true">{expanded ? '⌄' : '›'}</span></button>
      <div id="history-sessions" hidden={!expanded}>
        <button className="secondary my-2" onClick={onRefresh} disabled={loading}>Refresh history</button>
        {loading && <p role="status" className="sidebar-note">Loading conversations…</p>}
        {error && <p role="alert" className="sidebar-note">{error}</p>}
        {!loading && !error && !sessions.length && <p className="sidebar-note">No saved conversations yet.</p>}
        {sessions.map(item => <button key={item.id} data-session={item.id} className="history-session" aria-current={selected === item.id ? 'true' : undefined} onClick={() => onSession(item.id)}>
          <span className="flex items-start gap-2"><strong className="min-w-0 flex-1 break-words">{item.title || 'Untitled conversation'}</strong>{!!counts[item.id] && <span className="activity-count" aria-label={`${counts[item.id]} unread task updates`}>{counts[item.id]}</span>}</span>
          <span className="mt-2 block text-xs text-muted">{item.mode === 'Chat History' ? 'Private chat' : item.mode} · {item.source === 'telegram' ? 'Telegram' : 'Desktop'}<br />{new Date(item.created_at * 1000).toLocaleString()}{tasks.find(task => task.id === item.id) && <><br />{tasks.find(task => task.id === item.id)?.status}</>}</span>
        </button>)}
      </div>
    </nav>
    <button id="user" onClick={onSettings} aria-haspopup="dialog" className="flex min-h-[64px] items-center gap-3 border-t border-line px-3 text-left"><span className="grid size-8 place-items-center rounded-full bg-[#e5e3dc]">{user[0]?.toUpperCase()}</span><span className="min-w-0 flex-1 truncate">{user}</span><span className="text-xs text-muted">Settings</span></button>
  </aside>;
}
