import { useEffect, useRef, useState } from 'react';
import { useNativeBridge } from './bridge';
import { Sidebar } from './components/Sidebar';
import { Composer } from './components/Composer';
import { HistoryBrowser } from './components/HistoryBrowser';
import { TaskStatus } from './components/TaskStatus';
import { TaskFeed } from './components/TaskFeed';
import { EmptyState } from './components/EmptyState';
import { SettingsPanel, serviceLabel } from './components/SettingsPanel';
import type { Draft, View } from './types';
const fresh = (): Draft => ({ link: '', text: '', request_id: crypto.randomUUID() });
export function App() {
  const { result, busy, submitting, send } = useNativeBridge();
  const [view, setView] = useState<View>('Issue');
  const [drafts, setDrafts] = useState<Record<View, Draft>>({ Issue: fresh(), 'Pull Request': fresh(), 'Chat History': fresh() });
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [current, setCurrent] = useState<Partial<Record<View, string>>>({});
  const accepted = useRef('');
  const submitted = useRef<Partial<Record<View, Draft>>>({});
  useEffect(() => {
    const task = result.accepted;
    if (task) {
      setCurrent(previous => previous[task.mode] === task.id ? previous : ({ ...previous, [task.mode]: task.id }));
      if (task.id !== accepted.current) {
        accepted.current = task.id;
        setDrafts(previous => {
          const draft = previous[task.mode];
          const sent = submitted.current[task.mode];
          if (draft.request_id !== task.id || !sent || sent.request_id !== task.id) return previous;
          return { ...previous, [task.mode]: {
            ...fresh(), link: draft.link === sent.link ? '' : draft.link,
            text: draft.text === sent.text ? '' : draft.text,
          } };
        });
      }
    }
  }, [result.accepted]);
  useEffect(() => {
    send('tasks');
    const timer = window.setInterval(() => send('tasks'), 2000);
    return () => clearInterval(timer);
  }, [send]);
  const tasks = (result.tasks || []).filter(task => task.mode === view)
    .sort((a, b) => b.created_at - a.created_at);
  const selectedTask = tasks.find(task => task.id === current[view]) || tasks[0];
  useEffect(() => {
    if (view !== 'Chat History' && selectedTask && current[view] !== selectedTask.id) {
      setCurrent(previous => ({ ...previous, [view]: selectedTask.id }));
    }
  }, [view, selectedTask?.id, current]);
  const taskFeed = selectedTask && <TaskFeed key={selectedTask.id} task={selectedTask} busy={busy} send={send} />;
  const composer = <Composer mode={view} draft={drafts[view]} onChange={draft => setDrafts(previous => ({ ...previous, [view]: draft }))} busy={busy} submitting={submitting} onSend={() => { submitted.current[view] = { ...drafts[view] }; send('submit_message', { ...drafts[view], mode: view }); }} />;
  return <div className="flex min-h-screen bg-canvas text-ink">
    <Sidebar active={view} onSelect={setView} onSettings={() => setSettingsOpen(true)} user={result.status?.user || 'Local user'} />
    <main className="h-screen min-w-0 flex-1 overflow-y-auto"><header className="flex h-[76px] items-center justify-between px-7"><span className="text-xs text-muted">{view === 'Chat History' ? 'Conversations' : `${view} workspace`}</span><button id="status-button" className="secondary" onClick={() => setSettingsOpen(true)}><span id="service-label">{serviceLabel(result.status)}</span></button></header>
      <div className="mx-auto max-w-[760px] px-6 pb-8"><EmptyState view={view} />
        {view === 'Chat History' ? <HistoryBrowser result={result} send={send} busy={busy} composer={composer} /> : <>
          {tasks.length > 0 && <label className="mb-3 block text-xs text-muted">Current task <select aria-label="Current task" value={selectedTask?.id || ''} onChange={event => setCurrent(previous => ({ ...previous, [view]: event.target.value }))}>{tasks.map(task => <option key={task.id} value={task.id}>{task.title || task.id} · {task.status}</option>)}</select></label>}
          <section aria-label="Task status" className="mb-6 space-y-3">{taskFeed}</section>
          <TaskStatus task={selectedTask} />
          {composer}
          <p className="mt-3 text-xs text-muted">Send starts a new task. Use Continue task above to answer a waiting task. Previous messages are in Chat History.</p>
        </>}
        {result.status?.executor === 'reconnecting' && <p role="status" className="mt-3 text-xs text-red-700">Desktop task execution was interrupted. Retrying…</p>}
        <p id="feedback" role="status" className={`mt-5 text-xs leading-6 ${result.ok ? 'text-muted' : 'text-red-700'}`}>{result.message || (busy && !submitting ? 'Operation in progress…' : '')}</p>
      </div></main>
    <SettingsPanel open={settingsOpen} onClose={() => setSettingsOpen(false)} busy={busy} result={result} send={send} />
  </div>;
}
