import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

import { cn } from "@/lib/cn";

export function Markdown({ children, className }: { children: string; className?: string }) {
  return (
    <div className={cn("prose-answer", className)}>
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        components={{
          a: ({ href, children: linkChildren }) => (
            <a href={href} target="_blank" rel="noreferrer noopener">
              {linkChildren}
            </a>
          ),
          h1: ({ children: c }) => <p className="font-semibold">{c}</p>,
          h2: ({ children: c }) => <p className="font-semibold">{c}</p>,
          h3: ({ children: c }) => <p className="font-semibold">{c}</p>,
        }}
      >
        {children}
      </ReactMarkdown>
    </div>
  );
}
