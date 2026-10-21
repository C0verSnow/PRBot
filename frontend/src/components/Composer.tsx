import type { SendAction } from '../types';
import { Icon } from './Icon';
interface Props { draft: string; onChange: (value: string) => void; busy: boolean; send: SendAction; hidden: boolean }
export function Composer({ draft, onChange, busy, send, hidden }: Props) {
  return <section id="new-view" hidden={hidden} aria-labelledby="greeting" className="mx-auto mt-[clamp(88px,17vh,155px)] w-[calc(100%_-_72px)] max-w-[690px] max-[940px]:w-[calc(100%_-_44px)]">
    <div className="mb-9 flex items-center justify-center gap-3 max-[940px]:gap-2"><span aria-hidden="true" className="font-serif text-5xl leading-none text-accent max-[940px]:text-4xl">✳</span><h1 id="greeting" className="font-serif text-[clamp(26px,3.2vw,42px)] leading-[1.2] tracking-[-1.3px] max-[940px]:tracking-[-.8px]">What shall we think through?</h1></div>
    <form id="composer" className="composer rounded-[17px] border border-[#dedcd6] bg-white px-[17px] pt-4 pb-[11px] shadow-[0_4px_20px_#23211a04] focus-within:border-[#bcb8ad] focus-within:shadow-[0_4px_22px_#23211a08]" onSubmit={event => { event.preventDefault(); send('draft', draft); }}>
      <label htmlFor="draft" className="sr-only">GitHub 仓库链接与需求</label>
      <textarea id="draft" value={draft} onChange={event => onChange(event.target.value)} maxLength={3500} placeholder="粘贴 GitHub 仓库链接，描述你想完成的事…" spellCheck={false} className="block max-h-[230px] min-h-[66px] w-full resize-y border-0 bg-transparent p-0 leading-[1.65] text-ink outline-none placeholder:text-[#94918a]" />
      <div className="mt-2 flex items-center justify-between text-xs text-muted"><span>PRBot · 仓库任务</span><button id="handoff" type="submit" disabled={busy} title="复制需求并打开 Telegram" aria-label="复制需求并打开 Telegram" className="grid size-8 place-items-center rounded-[9px] bg-accent text-white hover:bg-[#b9694e]"><Icon name="arrow" /></button></div>
    </form>
    <p className="mx-auto mt-[18px] max-w-[560px] text-center text-[11px] leading-[1.9] text-muted">在这里整理需求，通过 Telegram 提交。<br />点击箭头复制需求并打开 Bot，先发送 <code>/FinishIssue</code>，收到提示后粘贴。</p>
  </section>;
}
