import { useEffect, useRef, useState } from 'react';
import { useNativeBridge } from './bridge';
import { Sidebar } from './components/Sidebar';
import { Composer } from './components/Composer';
import { EmptyState } from './components/EmptyState';
import { SettingsPanel, serviceLabel } from './components/SettingsPanel';
import type { Draft, Message, View } from './types';
const fresh = (): Draft => ({ link: '', text: '', request_id: crypto.randomUUID() });
export function App() {
  const { result, busy, send } = useNativeBridge();
  const [view, setView] = useState<View>('Issue');
  const [drafts, setDrafts] = useState<Record<View, Draft>>({ Issue: fresh(), 'Pull Request': fresh() });
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [histories, setHistories] = useState<Record<View, Message[]>>({ Issue: [], 'Pull Request': [] });
  const [cursors, setCursors] = useState<Record<View, string>>({ Issue: '0', 'Pull Request': '0' });
  const accepted = useRef('');
  useEffect(() => {
    const task = result.accepted;
    if (task && task.id !== accepted.current) {
      accepted.current = task.id;
      setDrafts(previous => previous[task.mode].request_id === task.id ? { ...previous, [task.mode]: fresh() } : previous);
      send('history', { mode: task.mode });
    }
  }, [result.accepted, send]);
  useEffect(() => {
    const page = result.history;
    if (page) {
      setHistories(previous => ({ ...previous, [page.mode]: Array.from(new Map(
        [...previous[page.mode], ...page.messages].map(message => [message.message_id, message])
      ).values()).sort((a, b) => a.sequence - b.sequence) }));
      setCursors(previous => ({ ...previous, [page.mode]: page.cursor }));
    }
  }, [result.history]);
  const requestedHistory = useRef<View | null>(null);
  useEffect(() => {
    if (!busy && requestedHistory.current !== view) {
      requestedHistory.current = view;
      send('history', { mode: view });
    }
  }, [view, busy, send]);
  useEffect(() => { const timer = window.setInterval(() => { if (!busy) send('tasks'); }, 4000); return () => clearInterval(timer); }, [send, busy]);
  const tasks = result.tasks?.filter(task => task.mode === view) || [];
  return <div className="flex min-h-screen bg-canvas text-ink">
    <Sidebar active={view} onSelect={setView} onSettings={() => setSettingsOpen(true)} user={result.status?.user || 'Local user'} />
    <main className="h-screen min-w-0 flex-1 overflow-y-auto"><header className="flex h-[76px] items-center justify-between px-7"><span className="text-xs text-muted">{view} workspace</span><button id="status-button" className="secondary" onClick={() => setSettingsOpen(true)}><span id="service-label">{serviceLabel(result.status)}</span></button></header>
      <div className="mx-auto max-w-[760px] px-6 pb-8"><EmptyState view={view} />
        <section aria-label="Conversation history" className="mb-6 space-y-3">{histories[view].map(message => <article key={message.message_id} className="rounded-xl border border-line p-4"><p className="mb-2 text-xs text-muted">{message.role === 'user' ? 'You' : 'Codex'}</p><p className="whitespace-pre-wrap break-words text-sm leading-7">{message.content}</p></article>)}{cursors[view] !== '0' && <button className="secondary" disabled={busy} onClick={() => send('history', { mode: view, cursor: cursors[view] })}>Load more messages</button>}</section>
        <section aria-label="Task status" aria-live="polite" className="mb-6 space-y-3">{tasks.map(task => <article key={task.id} className="rounded-xl border border-line bg-white p-4"><p className="text-xs">{task.id} · {task.status.replace('_', ' ')}</p><p className="mt-2 whitespace-pre-wrap break-words text-sm leading-7">{task.result}</p>{task.status === 'save_pending' && <button className="secondary mt-3" disabled={busy} onClick={() => send('retry_save', { text: task.id })}>Message not saved. Retry.</button>}</article>)}</section>
        <Composer mode={view} draft={drafts[view]} onChange={draft => setDrafts(previous => ({ ...previous, [view]: draft }))} busy={busy} onSend={() => send('submit_message', { ...drafts[view], mode: view })} />
        <p id="feedback" role="status" className={`mt-5 text-xs leading-6 ${result.ok ? 'text-muted' : 'text-red-700'}`}>{result.message}</p>
        {!result.ok && <button className="secondary mt-2" disabled={busy} onClick={() => send('history', { mode: view })}>Retry loading history</button>}
      </div></main>
    <SettingsPanel open={settingsOpen} onClose={() => setSettingsOpen(false)} busy={busy} result={result} send={send} />
  </div>;
}
