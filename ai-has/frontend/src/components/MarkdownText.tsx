"use client";

import React from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

export default function MarkdownText({ children }: { children: string }) {
  return (
    <div className="break-words leading-relaxed">
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        components={{
          h1: (p) => <h3 className="mt-3 mb-1 text-base font-bold" {...p} />,
          h2: (p) => <h3 className="mt-3 mb-1 text-base font-bold" {...p} />,
          h3: (p) => <h4 className="mt-3 mb-1 text-sm font-bold" {...p} />,
          h4: (p) => <h4 className="mt-2 mb-1 text-sm font-semibold" {...p} />,
          p: (p) => <p className="my-1.5" {...p} />,
          ul: (p) => <ul className="my-1.5 list-disc space-y-0.5 pl-5" {...p} />,
          ol: (p) => <ol className="my-1.5 list-decimal space-y-0.5 pl-5" {...p} />,
          strong: (p) => <strong className="font-semibold" {...p} />,
          a: (p) => <a className="underline" target="_blank" rel="noreferrer" {...p} />,
          hr: () => <hr className="my-2 border-slate-200" />,
          code: (p) => (
            <code className="rounded bg-slate-200/70 px-1 py-0.5 font-mono text-[0.85em]" {...p} />
          ),
          pre: (p) => (
            <pre className="my-2 overflow-x-auto rounded bg-slate-100 p-2 text-xs" {...p} />
          ),
          table: (p) => (
            <div className="my-2 overflow-x-auto">
              <table className="min-w-full border-collapse text-xs" {...p} />
            </div>
          ),
          th: (p) => <th className="border border-slate-300 bg-slate-100 px-2 py-1 text-left" {...p} />,
          td: (p) => <td className="border border-slate-300 px-2 py-1" {...p} />,
        }}
      >
        {children}
      </ReactMarkdown>
    </div>
  );
}
