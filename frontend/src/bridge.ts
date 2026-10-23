import { useCallback, useEffect, useRef, useState } from 'react';
import type { Action, NativeResult, SendAction } from './types';
export function useNativeBridge() {
  const [result, setResult] = useState<NativeResult>({ ok: true });
  const [busy, setBusy] = useState(false);
  const pending = useRef(false);
  const activeAction = useRef<Action | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const send = useCallback<SendAction>((action, data = {}) => {
    if (pending.current && action !== 'ready') return;
    const handler = window.webkit?.messageHandlers.prbot;
    if (!handler) {
      setResult(previous => ({ ...previous, ok: false, message: 'Open PRBot for macOS to use this feature.' }));
      return;
    }
    if (action !== 'ready') {
      pending.current = true;
      activeAction.current = action;
      setBusy(true);
      setSubmitting(action === 'submit_message');
    }
    try { handler.postMessage({ action, ...data }); }
    catch {
      pending.current = false; activeAction.current = null;
      setBusy(false); setSubmitting(false);
      setResult(previous => ({ ...previous, ok: false, message: 'Request could not be sent. Retry.' }));
    }
  }, []);
  useEffect(() => {
    let lastReport = Date.now();
    const receive = (payload: NativeResult) => {
      if (payload.status) lastReport = Date.now();
      if (payload.completed_action === activeAction.current) activeAction.current = null;
      pending.current = activeAction.current !== null || !!payload.busy;
      setBusy(pending.current);
      setSubmitting(activeAction.current === 'submit_message');
      setResult(previous => ({ ...previous, ...payload,
        message: payload.message ?? previous.message,
        ok: payload.message ? payload.ok : previous.ok,
      }));
    };
    window.receiveNative = receive;
    send('ready');
    const timer = window.setInterval(() => {
      if (Date.now() - lastReport > 15000) setResult(previous => ({ ...previous,
        status: { ...previous.status, state: 'stale' } }));
      if (!pending.current) send('ready');
    }, 3000);
    return () => { window.clearInterval(timer); if (window.receiveNative === receive) delete window.receiveNative; };
  }, [send]);
  return { result, busy, submitting, send };
}
