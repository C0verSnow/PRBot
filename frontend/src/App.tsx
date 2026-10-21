import { useState } from 'react';
import { useNativeBridge } from './bridge';
import { Composer } from './components/Composer';
import { EmptyState } from './components/EmptyState';
import { SettingsPanel, serviceLabel } from './components/SettingsPanel';
import { Sidebar } from './components/Sidebar';
import type { View } from './types';

export function App() {
  const [view, setView] = useState<View>('New');
  const [draft, setDraft] = useState('');
  const [settingsOpen, setSettingsOpen] = useState(false);
  const { result, busy, send } = useNativeBridge();
  const openSettings = () => { setSettingsOpen(true); send('ready'); };
  return <div className="flex h-screen overflow-hidden bg-canvas text-ink">
    <Sidebar active={view} onSelect={setView} onSettings={openSettings} user={result.status?.user || '用户'} />
    <main className="relative min-w-0 flex-1 overflow-auto">
      <header className="flex h-[78px] items-center justify-between gap-3 px-[27px] text-xs text-muted max-[940px]:justify-end max-[940px]:px-[22px]"><span className="max-[940px]:hidden">你的仓库，你的下一步。</span><button id="status-button" onClick={openSettings} className="flex items-center gap-[7px] rounded-full border border-line px-[11px] py-[7px] text-[11px] whitespace-nowrap hover:bg-[#eeece6]"><span className={`size-1.5 rounded-full ${result.status?.loaded && result.status.state === 'running' ? 'bg-[#6d8b68]' : 'bg-[#a19d93]'}`} /><span id="service-label">{serviceLabel(result.status)}</span></button></header>
      <Composer draft={draft} onChange={setDraft} busy={busy} send={send} hidden={view !== 'New'} />
      <EmptyState view={view} onNew={() => setView('New')} />
      <p id="feedback" role="status" aria-live="polite" className={`mx-auto mt-7 mb-5 max-w-[680px] px-7 text-xs leading-[1.9] whitespace-pre-wrap ${result.ok ? 'text-[#6d6a60]' : 'text-[#a74432]'}`}>{result.message}</p>
    </main>
    <SettingsPanel open={settingsOpen} onClose={() => setSettingsOpen(false)} busy={busy} result={result} send={send} />
  </div>;
}
