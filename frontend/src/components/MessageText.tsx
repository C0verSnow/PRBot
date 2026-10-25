export function MessageText({ content }: { content: string }) {
  const parts = content.split(/(```[^\n]*\n[\s\S]*?(?:```|$))/g);
  return <div className="message-text space-y-3 text-sm leading-7">{parts.map((part, index) => {
    if (!part) return null;
    if (part.startsWith('```')) {
      const start = part.indexOf('\n');
      const code = part.slice(start + 1).replace(/```$/, '');
      return <pre key={index} tabIndex={0} aria-label="Code snippet"><code>{code}</code></pre>;
    }
    return <p key={index} className="whitespace-pre-wrap break-words">{part}</p>;
  })}</div>;
}
