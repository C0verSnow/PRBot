import { useEffect, useRef, useState } from 'react';
import type { InfisicalFields, NativeResult, SendAction } from '../types';
const sections = ['Workspace', 'Connections', 'Configuration', 'Background Service'] as const;
const labels: Record<string, string> = { running: 'Running', starting: 'Starting', stopping: 'Stopping', stopped: 'Stopped', reconnecting: 'Reconnecting', stale: 'Stale', unknown: 'Unknown', unavailable: 'Unavailable', halted: 'Error', crashed: 'Error' };
export function serviceLabel(status: NativeResult['status']) { return labels[status?.state || 'unknown'] || 'Unknown'; }
interface Props { open: boolean; onClose: () => void; busy: boolean; result: NativeResult; send: SendAction }
export function SettingsPanel({ open, onClose, busy, result, send }: Props) {
  const dialog = useRef<HTMLDialogElement>(null);
  const [section, setSection] = useState<typeof sections[number]>('Workspace');
  const [token, setToken] = useState('');
  const [dirty, setDirty] = useState(false);
  const appliedToken = useRef('');
  const [confirmClose, setConfirmClose] = useState(false);
  const [configuration, setConfiguration] = useState<InfisicalFields | null>(null);
  const [configurationDirty, setConfigurationDirty] = useState(false);
  const savingConfiguration = useRef(false);
  const [closeAfterUse, setCloseAfterUse] = useState(false);
  const [transition, setTransition] = useState('');
  useEffect(() => { if (open && !dialog.current?.open) dialog.current?.showModal(); else if (!open) dialog.current?.close(); }, [open]);
  useEffect(() => {
    if (result.connections) {
      appliedToken.current = result.connections.id_token;
      if (!dirty) setToken(result.connections.id_token);
    }
    // Apply only a new connections response, never the prior response after Use token.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [result.connections]);
  useEffect(() => {
    const reply = result.id_token_result;
    if (!reply) return;
    setToken(reply.token); setDirty(!reply.applied);
    if (reply.applied) {
      appliedToken.current = reply.token; setConfirmClose(false);
      if (closeAfterUse) { setCloseAfterUse(false); onClose(); }
    }
    // Each result object is a completed native action; status polling retains its identity.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [result.id_token_result]);
  useEffect(() => {
    if (!result.configuration) return;
    if (!configurationDirty || savingConfiguration.current) {
      setConfiguration({ ...result.configuration.values, INFISICAL_CLIENT_SECRET: '' });
      setConfigurationDirty(false); savingConfiguration.current = false;
    }
    // Do not replace a draft when an unrelated status response arrives.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [result.configuration]);
  useEffect(() => { if (!busy) { setTransition(''); savingConfiguration.current = false; } }, [busy]);
  const close = () => { if (dirty || configurationDirty) setConfirmClose(true); else onClose(); };
  const state = transition || result.status?.state || 'unknown';
  const uncertain = ['unknown', 'stale', 'unavailable'].includes(state) && !result.status?.control_verified;
  const active = !!result.status?.loaded;
  const powerLabel = state === 'starting' ? 'Starting service' : state === 'stopping' ? 'Stopping service' : uncertain ? 'Refresh status' : active ? 'Stop service' : 'Start service';
  const choose = (value: typeof section) => {
    setSection(value);
    if (value === 'Connections' && !dirty) send('connections');
    if (value === 'Configuration' && !configurationDirty) send('configuration');
  };
  return <dialog ref={dialog} id="settings" aria-labelledby="settings-title" className="settings-dialog" onCancel={e => { e.preventDefault(); close(); }} onClose={() => { document.getElementById('user')?.focus(); onClose(); }}>
    <header className="flex items-center justify-between border-b border-line px-6 py-5"><h2 id="settings-title" className="font-serif text-2xl">Settings</h2><button id="close-settings" aria-label="Close settings" className="text-2xl" onClick={close}>×</button></header>
    <div className="settings-body"><nav aria-label="Settings sections" className="settings-nav">{sections.map(value => <button key={value} disabled={busy} data-section={value} className={`nav-item mb-1 ${section === value ? 'bg-[#eae8e2]' : ''}`} aria-current={section === value ? 'page' : undefined} onClick={() => choose(value)}>{value}</button>)}</nav>
      <div className="settings-content">
        {section === 'Workspace' && <section><h3 className="mb-7 text-lg font-medium">Workspace</h3>
          <div className="workspace-row"><div><h4>Working directory</h4><p>{result.status?.workspace || 'Not selected'}</p></div><button data-action="choose_workspace" className="secondary" disabled={busy} onClick={() => send('choose_workspace')}>Choose folder</button></div>
          <div className="workspace-row"><div><h4>Configuration directory</h4><p>{result.status?.configuration || 'Application configuration'}</p></div><button data-action="open_configuration" className="secondary" disabled={busy} onClick={() => send('open_configuration')}>Open folder</button></div>
        </section>}
        {section === 'Connections' && <section><h3 className="mb-7 text-lg font-medium">Connections</h3><p className="mb-5 text-sm">Infisical: {result.connections?.infisical || 'Not connected'}{result.connections ? ` · ${result.connections.project}` : ''}</p>
          <label htmlFor="id-token" className="mb-2 block font-medium">Codex id_token</label><input id="id-token" disabled={busy} type="text" value={token} autoComplete="off" spellCheck={false} onChange={e => { setToken(e.target.value); setDirty(true); }} placeholder="Sign in with Codex, then refresh" className="w-full rounded-lg border border-line bg-white px-3 py-2" />
          <p className="my-3 text-xs leading-6 text-muted">Read from ~/.codex/auth.json. The complete sign-in is used for SDK authentication. Fetch only reads the local ID token; it does not sign in or refresh credentials. Choose Use token to apply edits to new tasks in this window.</p>
          <div className="flex flex-wrap gap-2"><button id="fetch-codex-id-token" className="secondary" disabled={busy} onClick={() => send('fetch_codex_id_token')}>Fetch Codex ID Token</button><button className="secondary" disabled={busy || dirty} onClick={() => send('connections')}>Refresh connections</button><button className="secondary" disabled={busy || !dirty || !token.trim()} onClick={() => { send('use_id_token', { text: token }); }}>Use token</button></div>
          <p className="mt-8 text-xs leading-6 text-muted">Telegram and Redis secrets are managed in Infisical. GitHub uses the host Git sign-in or Infisical. No subscription tier check is performed.</p>
        </section>}
        {section === 'Configuration' && <section><h3 className="mb-5 text-lg font-medium">Configuration</h3>
          <p className="mb-4 text-xs leading-6 text-muted">Infisical connection settings for this workspace. Save explicitly, then restart PRBot to apply changes to all services. New desktop requests can pick up saved changes immediately; running tasks keep their current settings.</p>
          {configuration ? <form className="space-y-4" onSubmit={e => { e.preventDefault(); savingConfiguration.current = true; send('save_configuration', { configuration, configuration_path: result.configuration?.path }); }}>
            {([
              ['INFISICAL_URL', 'Infisical URL'], ['INFISICAL_CLIENT_ID', 'Client ID'],
              ['INFISICAL_CLIENT_SECRET', 'Client Secret'], ['INFISICAL_PROJECT_ID', 'Project ID'],
              ['INFISICAL_ENVIRONMENT', 'Environment'], ['INFISICAL_SECRET_PATH', 'Secret Path'],
            ] as const).map(([key, label]) => <div key={key}><label htmlFor={key} className="mb-1 block text-sm font-medium">{label}</label><input
              id={key} name={key} type={key === 'INFISICAL_CLIENT_SECRET' ? 'password' : key === 'INFISICAL_URL' ? 'url' : 'text'}
              required={key !== 'INFISICAL_CLIENT_SECRET' || !result.configuration?.client_secret_set} disabled={busy}
              autoComplete="off" spellCheck={false} value={configuration[key]} maxLength={20000}
              placeholder={key === 'INFISICAL_CLIENT_SECRET' && result.configuration?.client_secret_set ? 'Configured — leave blank to keep' : undefined}
              onChange={e => { setConfiguration(previous => previous && ({ ...previous, [key]: e.target.value })); setConfigurationDirty(true); }}
              className="w-full rounded-lg border border-line bg-white px-3 py-2 text-sm" />
              {key === 'INFISICAL_CLIENT_SECRET' && <p className="mt-1 text-xs text-muted">{result.configuration?.client_secret_set ? 'Client Secret is configured. Leave blank to keep the saved value.' : 'Client Secret is not configured.'}</p>}
            </div>)}
            {!!result.configuration?.environment_overrides.length && <p className="text-xs leading-6 text-muted">Process environment variables override saved values: {result.configuration.environment_overrides.join(', ')}.</p>}
            <p className="text-xs leading-6 text-muted">Use HTTPS for remote services. HTTP sends credentials without encryption. Saved in the selected configuration directory with owner-only permissions.</p>
            <div className="flex flex-wrap gap-2"><button id="save-configuration" type="submit" className="secondary" disabled={busy || !configurationDirty}>Save configuration</button><button type="button" className="secondary" disabled={busy || configurationDirty} onClick={() => send('configuration')}>Reload configuration</button></div>
          </form> : <button className="secondary" disabled={busy} onClick={() => send('configuration')}>Load configuration</button>}
        </section>}
        {section === 'Background Service' && <section className="relative min-h-[300px]"><h3 className="text-lg font-medium">Background Service</h3><button id="service-power" aria-label={powerLabel} disabled={busy || ['starting', 'stopping'].includes(state)} className="power-button" onClick={() => { if (uncertain) send('refresh_service'); else { setTransition(active ? 'stopping' : 'starting'); send(active ? 'stop_service' : 'start_service'); } }}>⏻</button>
          <div className="mt-16 text-center"><div aria-hidden="true" className={`service-core ${state}`} /><p className="mt-6 text-lg" aria-live="polite">{labels[state] || 'Unknown'}</p><p className="mt-3 text-xs text-muted">{state === 'running' ? 'Telegram is connected and listening.' : state === 'stale' ? 'The health report is out of date. Refresh status.' : 'Desktop tasks run independently of Telegram.'}</p><p className="mt-3 text-xs text-muted">Last report: {result.status?.reported_at ? new Date(result.status.reported_at * 1000).toLocaleTimeString('en-US') : 'Not available'}</p></div>
        </section>}
      </div></div>
    <footer id="settings-feedback" role="status" className="border-t border-line px-6 py-4 text-xs leading-6">{result.message || 'Changes and connection feedback appear here.'}</footer>
    {confirmClose && <div className="border-t border-line p-4" role="alert"><p>You have unsaved settings. Return to save them, or discard changes before closing.</p><div className="mt-3 flex gap-3">{dirty && !configurationDirty && <button className="secondary" disabled={busy} onClick={() => { setCloseAfterUse(true); send('use_id_token', { text: token }); }}>Use token</button>}<button className="secondary" disabled={busy} onClick={() => { setConfigurationDirty(false); setConfiguration(result.configuration ? { ...result.configuration.values, INFISICAL_CLIENT_SECRET: '' } : null); setDirty(false); setToken(appliedToken.current); setCloseAfterUse(false); setConfirmClose(false); onClose(); }}>Discard</button><button className="secondary" onClick={() => { setConfirmClose(false); setCloseAfterUse(false); }}>Cancel</button></div></div>}
  </dialog>;
}
