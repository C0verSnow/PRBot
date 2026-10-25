import { useCallback, useEffect, useRef, useState } from 'react';
import { useNativeBridge } from './bridge';
import { Sidebar } from './components/Sidebar';
import { Composer } from './components/Composer';
import { HistoryBrowser } from './components/HistoryBrowser';
import { TaskStatus } from './components/TaskStatus';
import { TaskFeed } from './components/TaskFeed';
import { SettingsPanel, serviceLabel } from './components/SettingsPanel';
import type { AnswerDraft, Draft, HistorySession, Workspace } from './types';
const fresh = (workspace: Workspace = 'GitHub'): Draft => ({ link: '', text: '', request_id: crypto.randomUUID(), workspace, created_at: Date.now() / 1000 });
export function App() {
  const { result, busy, submitting, cancelling, send } = useNativeBridge();
  const [initial] = useState(() => fresh());
  const [view, setView] = useState<Workspace>('GitHub');
  const [drafts, setDrafts] = useState<Record<string, Draft>>({ [initial.request_id]: initial });
  const [answers, setAnswers] = useState<Record<string, AnswerDraft>>({});
  const [selected, setSelected] = useState(initial.request_id);
  const [expanded, setExpanded] = useState(true);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const stoppingTask = useRef('');
  const [cancelOpen, setCancelOpen] = useState(false);
  const [sessions, setSessions] = useState<HistorySession[]>([]);
  const [historyLoading, setHistoryLoading] = useState(true);
  const [historyError, setHistoryError] = useState('');
  const [refresh, setRefresh] = useState(0);
  const [seen, setSeen] = useState<Record<string, string>>({});
  const submitted = useRef<Draft | null>(null);
  const submissions = useRef(new Map<string, Draft>());
  const cancelDialog = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    if (cancelOpen) cancelDialog.current?.showModal();
  }, [cancelOpen]);
  const accepted = useRef('');
  const selectedTask = result.tasks?.find(task => task.id === selected);
  const draft = drafts[selected] || { link: '', text: '', request_id: selected };
  const currentSubmitting = submitting && submitted.current?.request_id === draft.request_id;
  const onSessions = useCallback((items: HistorySession[] | undefined, loading: boolean, error: string) => {
    if (items) setSessions(items);
    setHistoryLoading(loading); setHistoryError(error);
  }, []);
  useEffect(() => {
    send('tasks');
    const timer = window.setInterval(() => send('tasks'), 1000);
    return () => clearInterval(timer);
  }, [send]);
  useEffect(() => {
    const task = result.accepted;
    if (!task || task.id === accepted.current) return;
    accepted.current = task.id;
    setRefresh(value => value + 1);
  }, [result.accepted]);
  useEffect(() => {
    // Polling can acknowledge a submission whose bridge reply timed out or was lost.
    // Retire its idempotency key once the task exists, while preserving any edits
    // made after a timeout. A later accepted reply must not clear those edits.
    const tasks = [...(result.tasks || []), ...(result.accepted ? [result.accepted] : [])];
    setDrafts(previous => {
      let next = previous;
      for (const task of tasks) {
        const current = previous[task.id];
        if (!current || current.request_id !== task.id) continue;
        const sent = submissions.current.get(task.id);
        const replacement = fresh(current.workspace);
        if (!sent || current.link !== sent.link || current.text !== sent.text) {
          replacement.link = current.link; replacement.text = current.text;
        }
        next = { ...next, [task.id]: replacement };
      }
      return next;
    });
  }, [result.tasks, result.accepted]);
  // A badge counts unread task state revisions (including one new accepted task),
  // not timer ticks or repeated event delivery. Reading a session acknowledges it.
  const counts: Record<string, number> = {};
  for (const task of result.tasks || []) {
    const revision = `${task.status}:${task.changed_at || task.created_at}`;
    counts[task.id] = task.id !== selected && seen[task.id] !== revision ? 1 : 0;
  }
  useEffect(() => {
    // Keep answer text and its retry key across navigation, until the backend
    // confirms that this task has left its waiting-for-answer state.
    setAnswers(previous => {
      const finished = (result.tasks || []).filter(task => previous[task.id] && (task.status !== 'needs_clarification' || previous[task.id].revision !== `${task.changed_at || task.created_at}:${task.result || ''}`));
      if (!finished.length) return previous;
      const next = { ...previous };
      for (const task of finished) delete next[task.id];
      return next;
    });
  }, [result.tasks]);
  const selectedRevision = selectedTask ? `${selectedTask.status}:${selectedTask.changed_at || selectedTask.created_at}` : '';
  useEffect(() => {
    if (selectedRevision) setSeen(previous => previous[selected] === selectedRevision ? previous : { ...previous, [selected]: selectedRevision });
  }, [selected, selectedRevision]);
  const newSession = (mode = view) => {
    const next = fresh(mode); setDrafts(previous => ({ ...previous, [next.request_id]: next }));
    setSelected(next.request_id); setView(mode);
  };
  const chooseSession = (id: string) => {
    const item = sessions.find(session => session.id === id) || result.tasks?.find(task => task.id === id);
    const mode = drafts[id]?.workspace || (item?.mode === 'Chat History' ? 'Chat History' : 'GitHub');
    if (!drafts[id]) setDrafts(previous => ({ ...previous, [id]: fresh(mode) }));
    setSelected(id); setView(mode);
  };
  const entries = new Map(sessions.map(item => [item.id, item]));
  for (const task of result.tasks || []) entries.set(task.id, { id: task.id, title: task.title || task.id, mode: task.mode, source: task.source || 'desktop', created_at: task.created_at });
  for (const [id, item] of Object.entries(drafts)) if (!entries.has(id) && (item.link || item.text)) entries.set(id, { id, title: `Draft · ${(item.text || item.link).slice(0, 120)}`, mode: item.workspace === 'Chat History' ? 'Chat History' : 'Issue', source: 'desktop', created_at: item.created_at || 0 });
  const sessionList = [...entries.values()].sort((a, b) => b.created_at - a.created_at);
  const optimistic = currentSubmitting ? submitted.current : null;
  return <div className="flex min-h-screen bg-canvas text-ink">
    <Sidebar active={view} onSelect={mode => newSession(mode)} onSettings={() => setSettingsOpen(true)} user={result.status?.user || 'Local user'} expanded={expanded} onToggle={() => setExpanded(value => !value)} sessions={sessionList} selected={selected} onSession={chooseSession} counts={counts} loading={historyLoading} error={historyError} onRefresh={() => setRefresh(value => value + 1)} tasks={result.tasks || []} />
    <main className="h-screen min-w-0 flex-1 overflow-y-auto"><header className="flex h-[76px] items-center justify-between px-7"><span className="text-xs text-muted">{view === 'GitHub' ? 'GitHub workspace' : 'Private chat'}</span><button id="status-button" className="secondary" onClick={() => setSettingsOpen(true)}><span id="service-label">{serviceLabel(result.status)}</span></button></header>
      <div className="mx-auto max-w-[760px] px-6 pb-8">
        <h1 className="mb-6 break-words font-serif text-[30px]">{selectedTask?.title || (view === 'GitHub' ? 'What would you like to change?' : 'Start a private conversation.')}</h1>
        <HistoryBrowser result={result} send={send} busy={busy} session={selected} refreshKey={`${refresh}:${selectedTask?.status || ''}`} onSessions={onSessions} task={selectedTask} optimistic={optimistic} />
        <section aria-label="Task status" className="mb-6 space-y-3">{selectedTask && <TaskFeed key={selectedTask.id} task={selectedTask} busy={busy} send={send} answer={answers[selectedTask.id] || { text: '', request_id: '' }} onAnswerChange={text => setAnswers(previous => ({ ...previous, [selectedTask.id]: { text, request_id: previous[selectedTask.id]?.request_id || crypto.randomUUID(), revision: `${selectedTask.changed_at || selectedTask.created_at}:${selectedTask.result || ''}` } }))} />}</section>
        <TaskStatus task={selectedTask} submitting={currentSubmitting} />
        <Composer stopPending={cancelling && stoppingTask.current === selected} mode={view} draft={draft} task={selectedTask} onChange={value => setDrafts(previous => ({ ...previous, [selected]: value }))} busy={busy} submitting={currentSubmitting} onNew={() => newSession()} onCancel={() => setCancelOpen(true)} onStop={() => { if (selectedTask && !cancelling) { stoppingTask.current = selectedTask.id; send('cancel_task', { task_id: selectedTask.id }); } }} onSend={() => {
          submitted.current = { ...draft };
          submissions.current.set(draft.request_id, { ...draft });
          // Every new submission has its own session/task identity. Continue task
          // answers use the existing task ID and saved conversation context.
          if (selected !== draft.request_id) { setSelected(draft.request_id); setDrafts(previous => ({ ...previous, [selected]: fresh(view), [draft.request_id]: draft })); }
          send('submit_message', { ...draft, mode: view });
        }} />
        <p className="mt-3 text-xs text-muted">Send starts an independent session. Use Continue task to answer Codex. New keeps background tasks running. History numbers count unread task state updates, at most one per session.</p>
        {result.status?.executor === 'reconnecting' && <p role="status" className="mt-3 text-xs text-red-700">Desktop task execution was interrupted. Retrying…</p>}
        <p id="feedback" role="status" className={`mt-5 text-xs leading-6 ${result.ok ? 'text-muted' : 'text-red-700'}`}>{result.message || (busy && !submitting ? 'Operation in progress…' : '')}</p>
      </div></main>
    {cancelOpen && <dialog ref={cancelDialog} className="draft-confirm" role="alertdialog" aria-labelledby="cancel-title" onCancel={() => setCancelOpen(false)}><div><h2 id="cancel-title">Discard unsent draft?</h2><p className="my-4">Saved history and running tasks are kept.</p><button autoFocus className="secondary mr-3" onClick={() => setCancelOpen(false)}>Keep editing</button><button id="confirm-cancel" className="secondary" onClick={() => { setDrafts(previous => ({ ...previous, [selected]: fresh(view) })); setCancelOpen(false); }}>Discard draft</button></div></dialog>}
    <SettingsPanel open={settingsOpen} onClose={() => setSettingsOpen(false)} busy={busy} result={result} send={send} />
  </div>;
}
