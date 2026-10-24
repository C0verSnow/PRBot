import { useEffect, useRef, useState } from 'react';
import type { Draft, Message, NativeResult, SendAction, Task } from '../types';
import { TaskResult } from './TaskResult';

export function HistoryBrowser({ result, send, busy, session, refreshKey, onSessions, task, optimistic }: {
  task?: Task; optimistic: Draft | null; result: NativeResult; send: SendAction; busy: boolean; session: string; refreshKey: string;
  onSessions: (sessions: NonNullable<NativeResult['history']>['sessions'], loading: boolean, error: string) => void;
}) {
  const [messages, setMessages] = useState<Message[]>([]);
  const [cursor, setCursor] = useState('0');
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [warning, setWarning] = useState('');
  const pending = useRef('');
  const loadRef = useRef<(cursor?: string) => void>(() => {});
  const load = (nextCursor = '0') => {
    const request_id = crypto.randomUUID();
    pending.current = request_id;
    setLoading(true); setError(''); onSessions(undefined, true, '');
    send('history', { mode: 'All', session, cursor: nextCursor, request_id });
  };
  loadRef.current = load;
  useEffect(() => { setMessages([]); setCursor('0'); setWarning(''); loadRef.current(); }, [session]);
  useEffect(() => { loadRef.current(); }, [refreshKey]);
  useEffect(() => {
    const page = result.history;
    if (!page || page.request_id !== pending.current || page.session !== session) return;
    setMessages(previous => {
      const merged = new Map<string, Message>();
      for (const message of [...(page.reset ? [] : previous), ...page.messages]) merged.set(`${message.conversation_id}:${message.message_id}`, message);
      return [...merged.values()].sort((a, b) => a.sequence - b.sequence || (a.logical_order || 0) - (b.logical_order || 0) || a.message_id.localeCompare(b.message_id));
    });
    setCursor(page.cursor); setWarning(page.warning || ''); setLoading(false); setError('');
    onSessions(page.sessions || [], false, '');
  }, [result.history, session, onSessions]);
  useEffect(() => {
    if (!result.history_error || result.history_error.request_id !== pending.current) return;
    setLoading(false); setError(result.history_error.message);
    onSessions(undefined, false, result.history_error.message);
  }, [result.history_error, onSessions]);
  // Draft sessions do not show unrelated archived messages while the index loads.
  return <section aria-label="Conversation history" className="mb-5 space-y-3" aria-busy={loading}>
    {loading && <p role="status" className="text-xs text-muted">Loading conversations…</p>}
    {error && <div role="alert"><p>{error}</p><button className="secondary" onClick={() => load(error === 'History changed. Refresh history.' ? '0' : cursor)}>Retry loading</button></div>}
    {warning && <p role="status" className="text-xs text-muted">{warning}</p>}
    {!loading && !error && messages.length === 0 && <p className="text-xs text-muted">No saved messages in this conversation.</p>}
    {!messages.some(message => message.role === 'user' && message.task_id === session) && (task || optimistic) && <article className="history-message"><p className="mb-2 text-xs text-muted">You{optimistic ? ' · Submitting…' : ''}</p><p className="whitespace-pre-wrap break-words">{optimistic?.text || task?.user_text || task?.title}</p></article>}
    {messages.filter(message => !(task?.result && message.role === 'assistant' && message.task_id === task.id && message.content === task.result)).map(message => <article key={`${message.conversation_id}:${message.message_id}`} className="history-message">
      <p className="mb-2 text-xs text-muted">{message.role === 'assistant' ? 'Codex' : 'You'} · {message.source === 'telegram' ? 'Telegram' : 'Desktop'} · {new Date(message.sequence * 1000).toLocaleString()}</p>
      {message.role === 'assistant' ? <TaskResult content={message.content} send={send} busy={busy} /> : <p className="whitespace-pre-wrap break-words text-sm leading-7">{message.content}</p>}
    </article>)}
    {cursor !== '0' && <button className="secondary" disabled={loading} onClick={() => load(cursor)}>Load more messages</button>}
  </section>;
}
