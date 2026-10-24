import { useLayoutEffect, useRef, useState } from 'react';
import type { SendAction, Task } from '../types';
import { TaskResult } from './TaskResult';
export function TaskFeed({ task, send, busy, archived = false, history = false }: { task: Task; send: SendAction; busy: boolean; archived?: boolean; history?: boolean }) {
  const feed = useRef<HTMLDivElement>(null);
  const follow = useRef(true);
  const [answer, setAnswer] = useState('');
  const replyId = useRef(crypto.randomUUID());
  useLayoutEffect(() => {
    if (follow.current && feed.current) feed.current.scrollTop = feed.current.scrollHeight;
  }, [task.events, task.result]);
  useLayoutEffect(() => {
    if (task.status === 'queued') { setAnswer(''); replyId.current = crypto.randomUUID(); }
  }, [task.status]);
  const label = task.status === 'delivered_unverified' ? 'Delivered · verification incomplete'
    : task.status === 'save_pending' ? 'Result not saved'
    : task.status === 'needs_clarification' ? 'Waiting for your answer'
    : task.status === 'timed_out' ? 'Timed out'
    : task.status.charAt(0).toUpperCase() + task.status.slice(1);
  return <article className="message-row py-4">
    <p className="mb-3 text-xs">{new Date(task.created_at * 1000).toLocaleString()} · {label}</p>
    <div ref={feed} className="execution-feed space-y-3" onScroll={() => {
      const node = feed.current;
      if (node) follow.current = node.scrollHeight - node.scrollTop - node.clientHeight < 64;
    }}>
      {!history && task.events?.map(event => <p key={event.sequence} className="whitespace-pre-wrap break-words text-xs leading-6 text-muted"><time>{new Date(event.created * 1000).toLocaleTimeString()}</time> · {event.text}</p>)}
      {task.result && !archived && <TaskResult content={task.result} send={send} busy={busy} />}
    </div>
    {task.status === 'needs_clarification' && task.source === 'telegram' && <p className="mt-3 text-sm">Reply to this task in Telegram to continue.</p>}
    {task.status === 'needs_clarification' && task.source !== 'telegram' && <form className="mt-4" onSubmit={event => {
      event.preventDefault(); if (answer.trim() && !busy) send('answer', { task_id: task.id, request_id: replyId.current, text: answer });
    }}><label className="block text-sm" htmlFor={`answer-${task.id}`}>Answer Codex to continue this task</label>
      <textarea id={`answer-${task.id}`} value={answer} maxLength={12000} onChange={event => setAnswer(event.target.value)} className="my-2 min-h-20 w-full rounded-lg border border-line p-3" />
      <button className="secondary" disabled={busy || !answer.trim()}>Continue task</button>
    </form>}
    {task.status === 'save_pending' && task.source !== 'telegram' && <button className="secondary mt-3" disabled={busy} onClick={() => send('retry_save', { text: task.id })}>Message not saved. Retry.</button>}
  </article>;
}
