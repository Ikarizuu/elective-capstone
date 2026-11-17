"use client";

import React, { useState } from "react";
import { BookOpen, ChevronDown, ChevronUp } from "lucide-react";
import { Citation } from "@/lib/api";
import MarkdownText from "./MarkdownText";

export function CitationBadgeList({ citations }: { citations: Citation[] }) {
  const [expandedId, setExpandedId] = useState<string | null>(null);

  if (!citations || citations.length === 0) return null;

  const activeCitation = citations.find((c) => c.chunk_id === expandedId);

  return (
    <div className="mt-3 border-t border-slate-200/80 pt-2.5">
      <div className="mb-1.5 flex items-center gap-1.5 text-[11px] font-semibold uppercase tracking-wide text-slate-500">
        <BookOpen className="h-3.5 w-3.5 text-blue-600" />
        <span>Grounded SOP Sources ({citations.length})</span>
      </div>

      <div className="flex flex-wrap gap-1.5">
        {citations.map((cite) => {
          const isOpen = expandedId === cite.chunk_id;
          const pct = Math.round(cite.score * 100);
          return (
            <button
              key={cite.chunk_id}
              type="button"
              onClick={() => setExpandedId(isOpen ? null : cite.chunk_id)}
              className={`inline-flex items-center gap-1.5 rounded-md border px-2 py-1 text-xs transition-colors ${
                isOpen
                  ? "border-blue-400 bg-blue-50 text-blue-800"
                  : "border-slate-200 bg-white text-slate-700 hover:border-blue-300 hover:bg-blue-50/50"
              }`}
            >
              <span className="font-semibold text-blue-700">{cite.doc_code}</span>
              <span className="max-w-[180px] truncate text-slate-600">
                {cite.section}
              </span>
              <span className="rounded bg-slate-100 px-1 py-0.5 text-[10px] font-medium text-slate-600">
                {pct}%
              </span>
              {isOpen ? (
                <ChevronUp className="h-3 w-3 text-slate-500" />
              ) : (
                <ChevronDown className="h-3 w-3 text-slate-500" />
              )}
            </button>
          );
        })}
      </div>

      {activeCitation && (
        <div className="mt-2 rounded-lg border border-blue-200 bg-blue-50/60 p-3 text-xs text-slate-700">
          <div className="mb-1 flex flex-wrap items-center justify-between gap-2">
            <span className="font-semibold text-slate-900">
              {activeCitation.title} — {activeCitation.section}
            </span>
            <span className="text-[11px] text-slate-500">
              Vector: {Math.round(activeCitation.vector_score * 100)}% · BM25:{" "}
              {Math.round(activeCitation.bm25_score * 100)}% · File: {activeCitation.filename}
            </span>
          </div>
          <div className="text-slate-700">
            <MarkdownText>{activeCitation.excerpt}</MarkdownText>
          </div>
        </div>
      )}
    </div>
  );
}
