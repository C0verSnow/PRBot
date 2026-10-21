import { useCallback, useEffect, useRef, useState } from 'react';
import type { NativeResult, SendAction } from './types';

export function useNativeBridge() {
  const [result, setResult] = useState<NativeResult>({ ok: true });
  const [busy, setBusy] = useState(false);
  const pending = useRef(false);
  const send = useCallback<SendAction>((action, text) => {
    if (pending.current && action !== 'ready') return;
    const handler = window.webkit?.messageHandlers.prbot;
    if (!handler) {
      setResult(previous => ({ ...previous, ok: false, message: '请在 PRBot macOS 应用中使用此功能。' }));
      return;
    }
    if (action !== 'ready') {
      pending.current = true;
      setBusy(true);
      setResult(previous => ({ ...previous, ok: true, message: '正在处理，请完成系统对话框中的操作…' }));
    }
    handler.postMessage({ action, text });
  }, []);

  useEffect(() => {
    const receive = (payload: NativeResult) => {
      pending.current = !!payload.busy;
      setBusy(!!payload.busy);
      setResult(previous => ({
        ...previous, ...payload,
        // Background status refreshes must preserve task handoff instructions/errors.
        message: payload.message ?? previous.message,
        ok: payload.message ? payload.ok : previous.ok,
      }));
    };
    window.receiveNative = receive;
    send('ready');
    const timer = window.setInterval(() => { if (!pending.current) send('ready'); }, 15000);
    return () => {
      window.clearInterval(timer);
      if (window.receiveNative === receive) delete window.receiveNative;
    };
  }, [send]);
  return { result, busy, send };
}
