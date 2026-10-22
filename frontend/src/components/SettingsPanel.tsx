import { useEffect, useRef, useState } from 'react';
import type { NativeResult, SendAction } from '../types';
const sections = ['Workspace', 'Connections', 'Background Service'] as const;
const labels: Record<string, string> = { running: 'Running', starting: 'Starting', stopping: 'Stopping', stopped: 'Stopped', reconnecting: 'Reconnecting', stale: 'Stale', unknown: 'Unknown', unavailable: 'Unavailable', halted: 'Error', crashed: 'Error' };
export function serviceLabel(status: NativeResult['status']) { return labels[status?.state || 'unknown'] || 'Unknown'; }
interface Props { open: boolean; onClose: () => void; busy: boolean; result: NativeResult; send: SendAction }
export function SettingsPanel({ open, onClose, busy, result, send }: Props) {
  const dialog = useRef<HTMLDialogElement>(null);
  const [section, setSection] = useState<typeof sections[number]>('Workspace');
  const [token, setToken] = useState('');
  const [dirty, setDirty] = useState(false);
  const [confirmClose, setConfirmClose] = useState(false);
  const [pendingUse, setPendingUse] = useState(false);
  const [closeAfterUse, setCloseAfterUse] = useState(false);
  const [transition, setTransition] = useState('');
  useEffect(() => { if (open && !dialog.current?.open) dialog.current?.showModal(); else if (!open) dialog.current?.close(); }, [open]);
  useEffect(() => {
    if (!result.connections) return;
    if (pendingUse && result.connections.id_token === token && result.ok) {
      setDirty(false); setPendingUse(false); setConfirmClose(false);
      if (closeAfterUse) { setCloseAfterUse(false); onClose(); }
    } else if (!dirty) setToken(result.connections.id_token);
  }, [result.connections, result.ok, dirty, pendingUse, token, closeAfterUse, onClose]);
  useEffect(() => { if (!busy) setTransition(''); }, [busy]);
  const close = () => { if (dirty) setConfirmClose(true); else onClose(); };
  const state = transition || result.status?.state || 'unknown';
  const uncertain = ['unknown', 'stale', 'unavailable'].includes(state);
  const active = !!result.status?.loaded;
  const powerLabel = uncertain ? 'Refresh status' : active ? 'Stop service' : 'Start service';
  const choose = (value: typeof section) => { setSection(value); if (value === 'Connections') send('connections'); };
  return <dialog ref={dialog} id="settings" aria-labelledby="settings-title" className="settings-dialog" onCancel={e => { e.preventDefault(); close(); }} onClose={() => { document.getElementById('user')?.focus(); onClose(); }}>
    <header className="flex items-center justify-between border-b border-line px-6 py-5"><h2 id="settings-title" className="font-serif text-2xl">Settings</h2><button id="close-settings" aria-label="Close settings" className="text-2xl" onClick={close}>×</button></header>
    <div className="settings-body"><nav aria-label="Settings sections" className="settings-nav">{sections.map(value => <button key={value} data-section={value} className={`nav-item mb-1 ${section === value ? 'bg-[#eae8e2]' : ''}`} aria-current={section === value ? 'page' : undefined} onClick={() => choose(value)}>{value}</button>)}</nav>
      <div className="settings-content">
        {section === 'Workspace' && <section><h3 className="mb-7 text-lg font-medium">Workspace</h3>
          <div className="workspace-row"><div><h4>Working directory</h4><p>{result.status?.workspace || 'Not selected'}</p></div><button data-action="choose_workspace" className="secondary" disabled={busy} onClick={() => send('choose_workspace')}>Choose folder</button></div>
          <div className="workspace-row"><div><h4>Configuration directory</h4><p>{result.status?.configuration || 'Application configuration'}</p></div><button data-action="open_configuration" className="secondary" disabled={busy} onClick={() => send('open_configuration')}>Open folder</button></div>
        </section>}
        {section === 'Connections' && <section><h3 className="mb-7 text-lg font-medium">Connections</h3><p className="mb-5 text-sm">Infisical: {result.connections?.infisical || 'Not connected'}{result.connections ? ` · ${result.connections.project}` : ''}</p>
          <label htmlFor="id-token" className="mb-2 block font-medium">Codex id_token</label><input id="id-token" type="text" value={token} autoComplete="off" spellCheck={false} onChange={e => { setToken(e.target.value); setDirty(true); }} placeholder="Sign in with Codex, then refresh" className="w-full rounded-lg border border-line bg-white px-3 py-2" />
          <p className="my-3 text-xs leading-6 text-muted">Read from ~/.codex/auth.json. The complete sign-in is used for SDK authentication. Edits stay in this window and apply to new tasks.</p>
          <div className="flex gap-2"><button className="secondary" disabled={busy || dirty} onClick={() => send('connections')}>Refresh connections</button><button className="secondary" disabled={busy || !dirty || !token.trim()} onClick={() => { setPendingUse(true); send('use_id_token', { text: token }); }}>Use token</button></div>
          <p className="mt-8 text-xs leading-6 text-muted">Telegram and Redis secrets are managed in Infisical. GitHub uses the host gh/git sign-in. No subscription tier check is performed.</p>
        </section>}
        {section === 'Background Service' && <section className="relative min-h-[300px]"><h3 className="text-lg font-medium">Background Service</h3><button id="service-power" aria-label={powerLabel} disabled={busy || ['starting', 'stopping'].includes(state)} className="power-button" onClick={() => { if (uncertain) send('ready'); else { setTransition(active ? 'stopping' : 'starting'); send(active ? 'stop_service' : 'start_service'); } }}>⏻</button>
          <div className="mt-16 text-center"><div aria-hidden="true" className={`service-core ${state}`} /><p className="mt-6 text-lg" aria-live="polite">{labels[state] || 'Unknown'}</p><p className="mt-3 text-xs text-muted">{state === 'running' ? 'Telegram is connected and listening.' : state === 'stale' ? 'The health report is out of date. Refresh status.' : 'Desktop tasks run independently of Telegram.'}</p><p className="mt-3 text-xs text-muted">Last report: {result.status?.reported_at ? new Date(result.status.reported_at * 1000).toLocaleTimeString('en-US') : 'Not available'}</p></div>
        </section>}
      </div></div>
    <footer id="settings-feedback" role="status" className="border-t border-line px-6 py-4 text-xs leading-6">{result.message || 'Changes and connection feedback appear here.'}</footer>
    {confirmClose && <div className="border-t border-line p-4" role="alert"><p>Use your edited ID token before closing?</p><div className="mt-3 flex gap-3"><button className="secondary" disabled={busy} onClick={() => { setCloseAfterUse(true); setPendingUse(true); send('use_id_token', { text: token }); }}>Use token</button><button className="secondary" onClick={() => { setDirty(false); setToken(result.connections?.id_token || ''); setConfirmClose(false); onClose(); }}>Discard</button><button className="secondary" onClick={() => setConfirmClose(false)}>Cancel</button></div></div>}
  </dialog>;
}
