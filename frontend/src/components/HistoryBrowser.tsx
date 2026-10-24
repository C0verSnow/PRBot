import { useCallback, useEffect, useRef, useState } from 'react';
import type { ReactNode } from 'react';
import type { HistoryMode, HistorySession, Message, NativeResult, SendAction } from '../types';
import { TaskFeed } from './TaskFeed';
import { TaskResult } from './TaskResult';

export function HistoryBrowser({ result, send, busy, composer }: {
  result: NativeResult; send: SendAction; busy: boolean; composer: ReactNode;
}) {
  const [mode, setMode] = useState<HistoryMode>('All');
  const [session, setSession] = useState('');
  const [sessions, setSessions] = useState<HistorySession[]>([]);
  const [messages, setMessages] = useState<Message[]>([]);
  const [cursor, setCursor] = useState('0');
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [warning, setWarning] = useState('');
  const [privateChat, setPrivateChat] = useState(false);
  const pending = useRef('');
  const load = useCallback((nextCursor = '0') => {
    const request_id = crypto.randomUUID();
    pending.current = request_id;
    setLoading(true); setError('');
    send('history', { mode, session, cursor: nextCursor, request_id });
  }, [mode, session, send]);
  useEffect(() => {
    setMessages([]); setCursor('0'); setWarning('');
    load();
  }, [load]);
  useEffect(() => {
    const page = result.history;
    if (!page || page.request_id !== pending.current) return;
    setMessages(previous => {
      const merged = new Map<string, Message>();
      for (const message of [...(page.reset ? [] : previous), ...page.messages]) {
        merged.set(`${message.conversation_id}:${message.message_id}`, message);
      }
      return [...merged.values()].sort((a, b) => a.sequence - b.sequence
        || (a.logical_order || 0) - (b.logical_order || 0) || a.message_id.localeCompare(b.message_id));
    });
    setSessions(page.sessions || []); setCursor(page.cursor);
    setWarning(page.warning || ''); setLoading(false); setError('');
  }, [result.history]);
  useEffect(() => {
    if (result.history_error?.request_id !== pending.current) return;
    setLoading(false); setError(result.history_error.message);
  }, [result.history_error]);
  const acceptedId = result.accepted?.id;
  const lastAccepted = useRef(acceptedId);
  useEffect(() => {
    if (acceptedId && acceptedId !== lastAccepted.current) load();
    lastAccepted.current = acceptedId;
  }, [acceptedId, load]);
  const selectedTask = (result.tasks || []).find(task => task.id === session);
  const privateTask = (result.tasks || []).filter(task => task.mode === 'Chat History' && task.source !== 'telegram')
    .sort((a, b) => b.created_at - a.created_at)[0];
  const hasResult = (taskId: string, content?: string) => messages.some(message => message.task_id === taskId && message.role === 'assistant' && message.content === content);
  return <>
    <section aria-label="Conversation history" className="space-y-4">
      <div className="flex flex-wrap items-center gap-3">
        <label className="text-xs">Show <select aria-label="History mode" value={mode} onChange={event => {
          setMode(event.target.value as HistoryMode); setSession(''); setSessions([]);
        }}><option value="All">All conversations</option><option value="Issue">Issues</option>
          <option value="Pull Request">Pull requests</option><option value="Chat History">Private chats</option></select></label>
        <button className="secondary" onClick={() => load()} disabled={loading}>Refresh history</button>
        <button className="secondary" aria-expanded={privateChat} onClick={() => setPrivateChat(value => !value)}>{privateChat ? 'Close new chat' : 'New private chat'}</button>
      </div>
      <div className="history-layout">
        <nav aria-label="Saved conversations" className="history-sessions">
          <button className="history-session" aria-current={!session ? 'true' : undefined} onClick={() => setSession('')}>All saved messages</button>
          {sessions.map(item => <button key={item.id} className="history-session" aria-current={session === item.id ? 'true' : undefined} onClick={() => setSession(item.id)}>
            <strong className="block whitespace-pre-wrap break-words">{item.title || 'Untitled conversation'}</strong>
            <span className="mt-2 block text-xs text-muted">{item.mode === 'Chat History' ? 'Private chat' : item.mode} · {item.source === 'telegram' ? 'Telegram' : 'Desktop'}<br />{new Date(item.created_at * 1000).toLocaleString()}</span>
          </button>)}
        </nav>
        <div aria-label="Saved messages" aria-busy={loading} className="min-w-0 space-y-3">
          {loading && <p role="status">Loading conversations…</p>}
          {error && <div role="alert"><p>{error}</p><button className="secondary mt-2" onClick={() => load(cursor)}>Retry loading</button></div>}
          {warning && <p role="status" className="text-xs text-muted">{warning}</p>}
          {!loading && !error && messages.length === 0 && <p className="py-6 text-sm text-muted">No saved messages in this view. Choose another filter or start a task from Issue or Pull Request.</p>}
          {messages.map(message => <article key={`${message.conversation_id}:${message.message_id}`} className="history-message">
            <p className="mb-2 text-xs text-muted">{message.role === 'assistant' ? 'Codex' : 'You'} · {message.source === 'telegram' ? 'Telegram' : 'Desktop'} · {message.mode === 'Chat History' ? 'Private chat' : message.mode} · {new Date(message.sequence * 1000).toLocaleString()}</p>
            <TaskResult content={message.content} send={send} busy={busy} />
          </article>)}
          {cursor !== '0' && <button className="secondary" disabled={loading} onClick={() => load(cursor)}>Load more messages</button>}
          {selectedTask && <TaskFeed key={selectedTask.id} task={selectedTask} send={send} busy={busy} history archived={hasResult(selectedTask.id, selectedTask.result)} />}
        </div>
      </div>
    </section>
    {privateChat && <section aria-label="New private chat" className="mt-6 space-y-3">
      <p className="text-sm">Start a new private conversation with Codex.</p>
      {privateTask && <TaskFeed key={privateTask.id} task={privateTask} send={send} busy={busy} />}
      {composer}
    </section>}
  </>;
}
