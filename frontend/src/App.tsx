import { useEffect, useRef, useState } from 'react';
import { useNativeBridge } from './bridge';
import { Sidebar } from './components/Sidebar';
import { Composer } from './components/Composer';
import { TaskResult } from './components/TaskResult';
import { TaskFeed } from './components/TaskFeed';
import { EmptyState } from './components/EmptyState';
import { SettingsPanel, serviceLabel } from './components/SettingsPanel';
import type { Draft, Message, View } from './types';
import { views } from './types';
const fresh = (): Draft => ({ link: '', text: '', request_id: crypto.randomUUID() });
export function App() {
  const { result, busy, submitting, send } = useNativeBridge();
  const [view, setView] = useState<View>('Issue');
  const [drafts, setDrafts] = useState<Record<View, Draft>>({ Issue: fresh(), 'Pull Request': fresh(), 'Chat History': fresh() });
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [histories, setHistories] = useState<Record<View, Message[]>>({ Issue: [], 'Pull Request': [], 'Chat History': [] });
  const [cursors, setCursors] = useState<Record<View, string>>({ Issue: '0', 'Pull Request': '0', 'Chat History': '0' });
  const [historyMode, setHistoryMode] = useState<View>('Chat History');
  const [session, setSession] = useState('');
  const [current, setCurrent] = useState<Partial<Record<View, string>>>({});
  const accepted = useRef('');
  useEffect(() => {
    const task = result.accepted;
    if (task) {
      setCurrent(previous => previous[task.mode] === task.id ? previous : ({ ...previous, [task.mode]: task.id }));
      if (task.mode === 'Chat History') { setHistoryMode('Chat History'); setSession(task.id); }
      if (task.id !== accepted.current) {
        accepted.current = task.id;
        setDrafts(previous => previous[task.mode].request_id === task.id ? { ...previous, [task.mode]: fresh() } : previous);
      }
    }
  }, [result.accepted]);
  useEffect(() => {
    const page = result.history;
    if (page) {
      setHistories(previous => ({ ...previous, [page.mode]: Array.from(new Map(
        [...previous[page.mode], ...page.messages].map(message => [message.message_id, message])
      ).values()).sort((a, b) => a.sequence - b.sequence) }));
      setCursors(previous => ({ ...previous, [page.mode]: page.cursor }));
    }
  }, [result.history]);
  useEffect(() => {
    if (view === 'Chat History') send('history', { mode: historyMode });
  }, [view, historyMode, send]);
  useEffect(() => {
    send('tasks');
    const timer = window.setInterval(() => send('tasks'), 2000);
    return () => clearInterval(timer);
  }, [send]);
  const historyTasks = result.tasks?.filter(task => task.mode === historyMode) || [];
  const tasks = (result.tasks || []).filter(task => view === 'Chat History'
    ? task.mode === historyMode && (!session || task.id === session)
    : task.mode === view && (task.id === current[view] || ['running', 'queued', 'needs_clarification', 'save_pending', 'interrupted'].includes(task.status))
  ).sort((a, b) => a.created_at - b.created_at);
  const messages = histories[historyMode].filter(message => !session || message.task_id === session);
  return <div className="flex min-h-screen bg-canvas text-ink">
    <Sidebar active={view} onSelect={setView} onSettings={() => setSettingsOpen(true)} user={result.status?.user || 'Local user'} />
    <main className="h-screen min-w-0 flex-1 overflow-y-auto"><header className="flex h-[76px] items-center justify-between px-7"><span className="text-xs text-muted">{view === 'Chat History' ? 'Conversations' : `${view} workspace`}</span><button id="status-button" className="secondary" onClick={() => setSettingsOpen(true)}><span id="service-label">{serviceLabel(result.status)}</span></button></header>
      <div className="mx-auto max-w-[760px] px-6 pb-8"><EmptyState view={view} />
        {view === 'Chat History' && <section aria-label="Conversation history" className="mb-6 space-y-3">
          <div className="flex flex-wrap gap-3"><label>History for <select aria-label="History workspace" value={historyMode} onChange={event => { setHistoryMode(event.target.value as View); setSession(''); }}>{views.map(mode => <option key={mode}>{mode}</option>)}</select></label>
            <label>Session <select aria-label="History session" value={session} onChange={event => setSession(event.target.value)}><option value="">All sessions</option>{historyTasks.map(task => <option key={task.id} value={task.id}>{new Date(task.created_at * 1000).toLocaleString()} · {task.status}</option>)}</select></label>
            <button className="secondary" onClick={() => send('history', { mode: historyMode })}>Refresh history</button></div>
          {messages.map(message => <article key={message.message_id} className="rounded-xl border border-line p-4"><p className="mb-2 text-xs text-muted">{message.role === 'user' ? 'You' : 'Codex'} · {new Date(message.sequence * 1000).toLocaleString()}</p>{message.role === 'assistant' ? <TaskResult content={message.content} send={send} busy={busy} /> : <p className="whitespace-pre-wrap break-words text-sm leading-7">{message.content}</p>}</article>)}
          {cursors[historyMode] !== '0' && <button className="secondary" onClick={() => send('history', { mode: historyMode, cursor: cursors[historyMode] })}>Load more messages</button>}
        </section>}
        <Composer mode={view} draft={drafts[view]} onChange={draft => setDrafts(previous => ({ ...previous, [view]: draft }))} busy={busy} submitting={submitting} onSend={() => send('submit_message', { ...drafts[view], mode: view })} />
        <section aria-label="Task status" aria-live="polite" className="mt-6 space-y-3">{tasks.map(task => <TaskFeed key={task.id} task={task} busy={busy} send={send} archived={view === 'Chat History' && messages.some(message => message.role === 'assistant' && message.task_id === task.id && message.content === task.result)} />)}</section>
        <p id="feedback" role="status" className={`mt-5 text-xs leading-6 ${result.ok ? 'text-muted' : 'text-red-700'}`}>{result.message}</p>
        {!result.ok && view === 'Chat History' && <button className="secondary mt-2" onClick={() => send('history', { mode: historyMode })}>Retry loading history</button>}
      </div></main>
    <SettingsPanel open={settingsOpen} onClose={() => setSettingsOpen(false)} busy={busy} result={result} send={send} />
  </div>;
}
