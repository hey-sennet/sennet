import Md, { type Components } from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { cn } from '@/lib/utils'

/** Agent replies in the timeline: GitHub-flavoured markdown set for reading at the panel's size. */
export function Markdown({ children, className }: { children: string; className?: string }) {
  return (
    <div className={cn('flex flex-col gap-3 [overflow-wrap:anywhere]', className)}>
      <Md remarkPlugins={[remarkGfm]} components={components}>{withoutSpoken(children)}</Md>
    </div>
  )
}

const components: Components = {
  h1: ({ children }) => <h3 className="mt-1 text-[1.15em] font-semibold">{children}</h3>,
  h2: ({ children }) => <h3 className="mt-1 text-[1.1em] font-semibold">{children}</h3>,
  h3: ({ children }) => <h4 className="mt-1 font-semibold">{children}</h4>,
  h4: ({ children }) => <h4 className="mt-1 font-semibold">{children}</h4>,
  ul: ({ children }) => <ul className="flex list-disc flex-col gap-1 pl-5 marker:text-muted-foreground">{children}</ul>,
  ol: ({ children }) => <ol className="flex list-decimal flex-col gap-1 pl-5 marker:text-muted-foreground">{children}</ol>,
  a: ({ children, href }) => <a href={href} target="_blank" rel="noreferrer" className="underline decoration-muted-foreground underline-offset-2 hover:decoration-foreground">{children}</a>,
  blockquote: ({ children }) => <blockquote className="border-l-2 pl-3 text-muted-foreground">{children}</blockquote>,
  hr: () => <hr className="border-border" />,
  pre: ({ children }) => (
    <pre className="overflow-x-auto rounded-lg bg-muted/60 px-3 py-2 font-mono text-xs leading-relaxed [overflow-wrap:normal] [&_code]:bg-transparent [&_code]:p-0">{children}</pre>
  ),
  code: ({ children }) => <code className="rounded bg-muted px-1 py-px font-mono text-[0.8em]">{children}</code>,
  table: ({ children }) => <div className="overflow-x-auto"><table className="w-full font-sans text-[13px]">{children}</table></div>,
  th: ({ children }) => <th className="border-b px-2 py-1 text-left font-medium text-muted-foreground">{children}</th>,
  td: ({ children }) => <td className="border-b px-2 py-1 align-top">{children}</td>,
}

/** A reply without the version the agent wrote to be heard, which voice speaks instead of reading
 *  out the reply. It is only ever at the start; while it streams it may not be closed yet. */
export function withoutSpoken(markdown: string): string {
  return markdown.replace(/^\s*<(?:s(?:p(?:o(?:k(?:e(?:n)?)?)?)?)?)?$/, '').replace(/^\s*<spoken>[\s\S]*?(?:<\/spoken>\s*|$(?![\s\S]))/, '')
}

/** A reply flattened to one line of prose for the stage: no markup, code blocks dropped. */
export function plainText(markdown: string): string {
  return withoutSpoken(markdown)
    .replace(/^[ \t]*(```|~~~)[^\n]*\n[\s\S]*?(?:^[ \t]*\1[ \t]*$|$(?![\s\S]))/gm, ' ')
    .replace(/!\[[^\]]*\]\([^)]*\)/g, '')
    .replace(/\[([^\]]+)\]\([^)]*\)/g, '$1')
    .replace(/`([^`\n]+)`/g, '$1')
    .replace(/(\*\*|__)(?=\S)(.+?)(?<=\S)\1/g, '$2')
    .replace(/(?<![\w*])\*(?=\S)(.+?)(?<=\S)\*(?![\w*])/g, '$1')
    .replace(/~~(.+?)~~/g, '$1')
    .replace(/:?\s*\n\s*(?:[-*+]|\d+[.)])\s+/g, ' · ')
    .replace(/^\s*(?:[-*+]|\d+[.)]|#{1,6}|>)\s+/gm, '')
    .replace(/^\s*\|.*\|\s*$/gm, '')
    .replace(/\s+/g, ' ')
    .trim()
}
