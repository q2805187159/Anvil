"use client";

import React, { useEffect, useId, useMemo, useState } from "react";
import ReactMarkdown from "react-markdown";
import rehypeKatex from "rehype-katex";
import remarkGfm from "remark-gfm";
import remarkMath from "remark-math";
import hljs from "highlight.js";

import { cn } from "@/src/lib/utils";

type WorkspaceRichContentProps = {
  content: string;
  className?: string;
};

export function WorkspaceRichContent({ content, className }: WorkspaceRichContentProps) {
  return (
    <div
      className={cn(
        "workspace-rich-content prose prose-neutral min-w-0 max-w-full overflow-hidden break-words text-[13px] leading-[1.55] [overflow-wrap:anywhere] dark:prose-invert",
        className,
      )}
    >
      <ReactMarkdown
        remarkPlugins={[remarkGfm, remarkMath]}
        rehypePlugins={[rehypeKatex]}
        components={{
          h1: ({ children }) => (
            <h1 className="mb-1.5 mt-3 break-words text-lg font-semibold tracking-tight text-[var(--ink)] [overflow-wrap:anywhere] first:mt-0">
              {children}
            </h1>
          ),
          h2: ({ children }) => (
            <h2 className="mb-1.5 mt-3 break-words text-base font-semibold tracking-tight text-[var(--ink)] [overflow-wrap:anywhere]">
              {children}
            </h2>
          ),
          h3: ({ children }) => (
            <h3 className="mb-1 mt-2.5 break-words text-sm font-semibold text-[var(--ink)] [overflow-wrap:anywhere]">{children}</h3>
          ),
          p: ({ children }) => (
            <p className="my-1.5 min-w-0 whitespace-pre-wrap break-words text-[13px] leading-[1.55] text-[var(--ink)]/92 [overflow-wrap:anywhere]">
              {children}
            </p>
          ),
          ul: ({ children }) => (
            <ul className="my-1.5 min-w-0 list-disc space-y-0.5 break-words pl-4 text-[13px] leading-[1.55] text-[var(--ink)]/92 [overflow-wrap:anywhere]">
              {children}
            </ul>
          ),
          ol: ({ children }) => (
            <ol className="my-1.5 min-w-0 list-decimal space-y-0.5 break-words pl-4 text-[13px] leading-[1.55] text-[var(--ink)]/92 [overflow-wrap:anywhere]">
              {children}
            </ol>
          ),
          li: ({ children }) => <li className="min-w-0 break-words [overflow-wrap:anywhere]">{children}</li>,
          table: ({ children }) => <table className="my-2 w-full table-fixed border-collapse text-[12px]">{children}</table>,
          th: ({ children }) => <th className="min-w-0 break-words border border-[var(--line)] px-2 py-1 text-left font-semibold [overflow-wrap:anywhere]">{children}</th>,
          td: ({ children }) => <td className="min-w-0 break-words border border-[var(--line)] px-2 py-1 align-top [overflow-wrap:anywhere]">{children}</td>,
          a: ({ href, children }) => <LinkRenderer href={href} text={String(children)} />,
          code: ({ className: languageClassName, children }) => {
            const match = /language-(\w+)/.exec(languageClassName || "");
            const value = String(children).replace(/\n$/, "");
            if (!match) {
              return (
                <code className="whitespace-pre-wrap break-all rounded-md border border-[var(--line)] bg-[var(--panel-muted)] px-1.5 py-0.5 font-[var(--mono-font)] text-[0.92em] text-[var(--ink)]">
                  {value}
                </code>
              );
            }
            if (match[1] === "mermaid") {
              return <MermaidBlock chart={value} />;
            }
            return <CodeBlock code={value} language={match[1]!} />;
          },
          pre: ({ children }) => <>{children}</>,
        }}
      >
        {content}
      </ReactMarkdown>
    </div>
  );
}

function LinkRenderer({ href, text }: { href?: string; text: string }) {
  const url = href ?? "";
  return (
    <a
      href={url}
      target="_blank"
      rel="noreferrer"
      className="inline max-w-full break-all font-medium text-[var(--primary)] underline decoration-[color-mix(in_srgb,var(--primary)_38%,transparent)] underline-offset-4 transition [overflow-wrap:anywhere] hover:decoration-[var(--primary)]"
    >
      {text}
    </a>
  );
}

function CodeBlock({ code, language }: { code: string; language: string }) {
  const [copied, setCopied] = useState(false);
  const lines = useMemo(() => code.split("\n"), [code]);

  async function handleCopy() {
    try {
      await navigator.clipboard.writeText(code);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 2000);
    } catch {
      setCopied(false);
    }
  }

  return (
    <div className="my-2 max-w-full overflow-hidden rounded-[0.7rem] border border-[var(--line)] bg-[var(--panel-strong)] shadow-[0_4px_6px_-2px_rgba(0,0,0,0.05),0_2px_4px_-1px_rgba(0,0,0,0.03)]">
      <div className="flex items-center justify-between border-b border-[var(--line)] bg-[color-mix(in_srgb,var(--panel)_84%,white_16%)] px-2.5 py-1">
        <span className="rounded-[0.55rem] border border-[var(--line)] bg-[var(--panel-muted)] px-2 py-0.5 font-[var(--mono-font)] text-[10px] font-semibold uppercase tracking-[0.06em] text-[var(--muted)]">
          {language}
        </span>
        <button
          type="button"
          onClick={() => void handleCopy()}
          className="rounded-full border border-[var(--line)] px-2.5 py-0.5 text-[11px] font-medium text-[var(--muted)] transition hover:bg-[var(--panel-muted)] hover:text-[var(--ink)]"
          aria-label="Copy code"
        >
          {copied ? "Copied" : "Copy code"}
        </button>
      </div>
      <div className="max-h-[400px] max-w-full overflow-auto">
        <table className="w-full table-fixed border-collapse font-[var(--mono-font)] text-[12px] leading-5">
          <tbody>
            {lines.map((line, index) => (
              <tr key={`${language}-${index}`} className="align-top">
                <td className="w-10 select-none border-r border-[var(--line)] bg-black/4 px-2 py-0 text-right text-[11px] text-[var(--muted)]">
                  {index + 1}
                </td>
                <td className="min-w-0 px-3 py-0">
                  <code
                    className="block whitespace-pre-wrap break-all text-[var(--ink)]"
                    dangerouslySetInnerHTML={{
                      __html: highlightLine(line, language),
                    }}
                  />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function MermaidBlock({ chart }: { chart: string }) {
  const id = useId().replace(/:/g, "-");
  const [svg, setSvg] = useState<string | null>(null);

  useEffect(() => {
    let active = true;

    async function renderChart() {
      try {
        const mermaid = (await import("mermaid")).default;
        mermaid.initialize({
          startOnLoad: false,
          theme: "base",
          securityLevel: "loose",
          fontFamily: "var(--body-font)",
          themeVariables: {
            primaryColor: "#10a37f",
            primaryTextColor: "#111827",
            primaryBorderColor: "#10a37f",
            lineColor: "#6b7280",
            secondaryColor: "#f9fafb",
            tertiaryColor: "#ffffff",
          },
        });
        const rendered = await mermaid.render(`mermaid-${id}`, chart);
        if (active) {
          setSvg(rendered.svg);
        }
      } catch {
        if (active) {
          setSvg(null);
        }
      }
    }

    void renderChart();
    return () => {
      active = false;
    };
  }, [chart, id]);

  return (
    <div className="my-3 max-w-full overflow-hidden rounded-[0.75rem] border border-[var(--line)] bg-[var(--panel-strong)] shadow-[0_4px_6px_-2px_rgba(0,0,0,0.05),0_2px_4px_-1px_rgba(0,0,0,0.03)]">
      <div className="flex items-center justify-between border-b border-[var(--line)] bg-[color-mix(in_srgb,var(--panel)_84%,white_16%)] px-3 py-1.5">
        <span className="text-[13px] font-semibold text-[var(--ink)]">Mermaid diagram</span>
      </div>
      <div className="max-w-full overflow-auto px-3 py-3">
        {svg ? (
          <div className="mermaid-output" dangerouslySetInnerHTML={{ __html: svg }} />
        ) : (
          <pre className="max-w-full overflow-auto whitespace-pre-wrap break-all rounded-xl bg-[var(--panel-muted)] p-4 font-[var(--mono-font)] text-sm text-[var(--muted)]">
            {chart}
          </pre>
        )}
      </div>
    </div>
  );
}

function highlightLine(line: string, language: string) {
  if (!line) {
    return "&nbsp;";
  }
  try {
    return hljs.highlight(line, { language }).value;
  } catch {
    try {
      return hljs.highlightAuto(line).value;
    } catch {
      return escapeHtml(line);
    }
  }
}

function escapeHtml(value: string) {
  return value
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;");
}
