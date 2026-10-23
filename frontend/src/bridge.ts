import { useCallback, useEffect, useRef, useState } from 'react';
import type { Action, NativeResult, RequestData, SendAction } from './types';
const background = new Set<Action>(['ready', 'tasks', 'history', 'connections', 'configuration']);
export function useNativeBridge() {
  const [result, setResult] = useState<NativeResult>({ ok: true });
  const [busy, setBusy] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const pending = useRef(new Map<Action, number>());
  const serial = useRef(0);
  const tasksRevision = useRef(0);
  const queuedHistory = useRef<RequestData | null>(null);
  const active = useRef<Action | null>(null);
  const send = useCallback<SendAction>((action, data = {}) => {
    if (pending.current.has(action)) {
      if (action === 'history') queuedHistory.current = data;
      return;
    }
    if (!background.has(action) && active.current) return;
    const handler = window.webkit?.messageHandlers.prbot;
    if (!handler) {
      setResult(previous => ({ ...previous, ok: false, message: 'Open PRBot for macOS to use this feature.' }));
      return;
    }
    const bridge_id = ++serial.current;
    pending.current.set(action, bridge_id);
    if (!background.has(action)) {
      active.current = action; setBusy(true);
      setSubmitting(action === 'submit_message' || action === 'answer');
    }
    try { handler.postMessage({ action, ...data, bridge_id }); }
    catch {
      pending.current.delete(action);
      if (active.current === action) { active.current = null; setBusy(false); setSubmitting(false); }
      setResult(previous => ({ ...previous, ok: false, message: 'Request could not be sent. Retry.' }));
    }
  }, []);
  useEffect(() => {
    let lastReport = Date.now();
    const receive = (payload: NativeResult) => {
      const action = payload.completed_action;
      if (action && payload.bridge_id !== undefined && pending.current.get(action) !== payload.bridge_id) return;
      if (payload.tasks_revision !== undefined) {
        if (payload.tasks_revision < tasksRevision.current) payload = { ...payload, tasks: undefined };
        else tasksRevision.current = payload.tasks_revision;
      }
      if (payload.status) lastReport = Date.now();
      if (action) pending.current.delete(action);
      if (action === active.current) { active.current = null; setBusy(false); setSubmitting(false); }
      setResult(previous => {
        const next = { ...previous };
        for (const [key, value] of Object.entries(payload)) {
          if (['busy', 'completed_action', 'bridge_id', 'tasks_revision'].includes(key) || value == null) continue;
          if (key === 'ok' && !payload.message) continue;
          // Keep object identity for unchanged status, tasks and configuration responses.
          // Explicit token actions still need to apply even if their value is unchanged.
          if (key === 'id_token_result' || JSON.stringify(previous[key as keyof NativeResult]) !== JSON.stringify(value)) {
            Object.assign(next, { [key]: value });
          }
        }
        return Object.keys(next).some(key => next[key as keyof NativeResult] !== previous[key as keyof NativeResult]) ? next : previous;
      });
      if (action === 'history' && queuedHistory.current) {
        const data = queuedHistory.current; queuedHistory.current = null;
        send('history', data);
      }
    };
    window.receiveNative = receive;
    send('ready');
    const timer = window.setInterval(() => {
      if (Date.now() - lastReport > 15000) setResult(previous => previous.status?.state === 'stale' ? previous : ({ ...previous,
        status: { ...previous.status, state: 'stale' } }));
      send('ready');
    }, 3000);
    return () => { window.clearInterval(timer); if (window.receiveNative === receive) delete window.receiveNative; };
  }, [send]);
  return { result, busy, submitting, send };
}
