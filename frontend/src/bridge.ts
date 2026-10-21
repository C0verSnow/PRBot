import { useCallback, useEffect, useRef, useState } from 'react';
import type { NativeResult, SendAction } from './types';
export function useNativeBridge() {
  const [result, setResult] = useState<NativeResult>({ ok: true });
  const [busy, setBusy] = useState(false);
  const pending = useRef(false);
  const send = useCallback<SendAction>((action, data = {}) => {
    if (pending.current && action !== 'ready') return;
    const handler = window.webkit?.messageHandlers.prbot;
    if (!handler) {
      setResult(previous => ({ ...previous, ok: false, message: 'Open PRBot for macOS to use this feature.' }));
      return;
    }
    if (action !== 'ready') { pending.current = true; setBusy(true); }
    handler.postMessage({ action, ...data });
  }, []);
  useEffect(() => {
    let lastReport = Date.now();
    const receive = (payload: NativeResult) => {
      if (payload.status) lastReport = Date.now();
      pending.current = !!payload.busy;
      setBusy(!!payload.busy);
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
  return { result, busy, send };
}
