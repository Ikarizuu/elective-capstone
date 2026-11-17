"use client";

import React, { useCallback, useEffect, useState } from "react";
import {
  Activity,
  Clock,
  Database,
  FileUp,
  Lock,
  RefreshCw,
  Search,
  ShieldAlert,
  ShieldCheck,
  Sparkles,
  Trash2,
  UserCheck,
  Webhook,
} from "lucide-react";
import {
  AnalyticsSummary,
  ApiError,
  AuditLog,
  Citation,
  deleteKBDocument,
  fetchAnalyticsSummary,
  fetchAuditLogs,
  fetchWebhookDeliveries,
  KBDocument,
  listKBDocuments,
  reindexKB,
  searchKB,
  uploadKBDocument,
  WebhookDelivery, describeError } from "@/lib/api";
import { useRole } from "@/components/RoleSwitcher";
import { KpiCharts } from "@/components/KpiCharts";

export default function AdminDashboardPage() {
  const { role, persona, setRole } = useRole();
  const [summary, setSummary] = useState<AnalyticsSummary | null>(null);
  const [auditLogs, setAuditLogs] = useState<AuditLog[]>([]);
  const [webhooks, setWebhooks] = useState<WebhookDelivery[]>([]);
  const [kbDocs, setKbDocs] = useState<KBDocument[]>([]);
  const [totalChunks, setTotalChunks] = useState(0);
  const [searchQuery, setSearchQuery] = useState("");
  const [searchResults, setSearchResults] = useState<Citation[]>([]);
  const [uploading, setUploading] = useState(false);
  const [reindexing, setReindexing] = useState(false);
  const [activeTab, setActiveTab] = useState<"audit" | "webhooks">("audit");
  const [rbacError, setRbacError] = useState<string | null>(null);
  const [statusBanner, setStatusBanner] = useState<string | null>(null);

  const loadDashboard = useCallback(async () => {
    try {
      const [sumRes, logRes, whRes, kbRes] = await Promise.all([
        fetchAnalyticsSummary(persona),
        fetchAuditLogs(persona),
        fetchWebhookDeliveries(persona),
        listKBDocuments(persona),
      ]);
      setRbacError(null);
      setSummary(sumRes);
      setAuditLogs(logRes.logs || []);
      setWebhooks(whRes.deliveries || []);
      setKbDocs(kbRes.documents || []);
      setTotalChunks(kbRes.total_chunks || 0);
    } catch (err) {
      if (err instanceof ApiError && err.status === 403) {
        setRbacError(err.message);
      }
    }
  }, [persona]);

  useEffect(() => {
    loadDashboard();
  }, [loadDashboard]);

  const handleFileUpload = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;
    setUploading(true);
    try {
      const res = await uploadKBDocument(persona, file);
      setStatusBanner(
        `Indexed '${res.document.filename}' (${res.document.chunk_count} chunks).`
      );
      await loadDashboard();
    } catch (err) {
      setStatusBanner(describeError(err, "Upload failed"));
    } finally {
      setUploading(false);
      e.target.value = "";
    }
  };

  const handleReindex = async () => {
    setReindexing(true);
    try {
      const res = await reindexKB(persona);
      setStatusBanner(
        `Re-indexed ${res.documents_indexed} SOP documents (${res.total_chunks} total chunks).`
      );
      await loadDashboard();
    } catch (err) {
      setStatusBanner(describeError(err, "Re-index failed"));
    } finally {
      setReindexing(false);
    }
  };

  const handleDeleteDoc = async (docId: string, filename: string) => {
    try {
      await deleteKBDocument(persona, docId);
      setStatusBanner(`Deleted '${filename}' from Knowledge Base.`);
      await loadDashboard();
    } catch (err) {
      setStatusBanner(describeError(err, "Delete failed"));
    }
  };

  const handleKBSearch = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!searchQuery.trim()) return;
    try {
      const res = await searchKB(persona, searchQuery.trim());
      setSearchResults(res.results || []);
    } catch {
      setSearchResults([]);
    }
  };

  if (rbacError) {
    return (
      <div className="mx-auto max-w-xl rounded-xl border border-rose-200 bg-rose-50 p-6 text-center shadow-sm">
        <Lock className="mx-auto h-10 w-10 text-rose-600" />
        <h1 className="mt-3 text-lg font-bold text-slate-900">
          RBAC Protection: Admin Observability & KB Manager
        </h1>
        <p className="mt-2 text-sm text-slate-700">{rbacError}</p>
        <p className="mt-1 text-xs text-slate-500">
          Current active role: <span className="font-mono font-semibold">{role}</span>
        </p>
        <button
          type="button"
          onClick={() => setRole("admin")}
          className="mt-4 inline-flex items-center gap-2 rounded-lg bg-blue-600 px-4 py-2 text-sm font-semibold text-white hover:bg-blue-700"
        >
          <UserCheck className="h-4 w-4" />
          <span>Switch to Admin (Jordan Taylor)</span>
        </button>
      </div>
    );
  }

  const kpis = summary?.kpis;

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-xl font-bold text-slate-900">
            Admin KPI Observability, Security Logs & Knowledge Base
          </h1>
          <p className="text-xs text-slate-500">
            Real-time operational metrics, RAG evaluation quality, guardrail audit trail, and SOP indexing.
          </p>
        </div>

        <button
          type="button"
          onClick={loadDashboard}
          className="inline-flex items-center gap-1.5 rounded-lg border border-slate-200 bg-white px-3 py-1.5 text-xs font-semibold text-slate-700 shadow-sm hover:bg-slate-50"
        >
          <RefreshCw className="h-3.5 w-3.5" />
          <span>Refresh Telemetry</span>
        </button>
      </div>

      {statusBanner && (
        <div className="flex items-center justify-between rounded-lg border border-blue-200 bg-blue-50 px-4 py-2 text-xs font-medium text-blue-900">
          <span>{statusBanner}</span>
          <button type="button" onClick={() => setStatusBanner(null)} className="underline">
            Dismiss
          </button>
        </div>
      )}

      {/* Top KPI Grid */}
      {kpis && (
        <>
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4">
            <div className="rounded-xl border border-slate-200 bg-white p-4 shadow-sm">
              <div className="flex items-center justify-between text-xs text-slate-500">
                <span>Ticket Deflection Rate</span>
                <Sparkles className="h-4 w-4 text-blue-600" />
              </div>
              <div className="mt-2 text-2xl font-bold text-slate-900">
                {kpis.deflection_rate_pct}%
              </div>
              <div className="mt-1 text-xs text-slate-500">
                {kpis.deflected_turns} / {kpis.total_chat_turns} AI turns resolved without escalation
              </div>
            </div>

            <div className="rounded-xl border border-slate-200 bg-white p-4 shadow-sm">
              <div className="flex items-center justify-between text-xs text-slate-500">
                <span>Mean Time to Resolution (MTTR)</span>
                <Clock className="h-4 w-4 text-emerald-600" />
              </div>
              <div className="mt-2 text-2xl font-bold text-slate-900">
                {kpis.avg_mttr_minutes} min
              </div>
              <div className="mt-1 text-xs text-slate-500">
                {kpis.resolved_tickets} resolved · {kpis.open_tickets} open · {kpis.escalated_tickets} escalated
              </div>
            </div>

            <div className="rounded-xl border border-slate-200 bg-white p-4 shadow-sm">
              <div className="flex items-center justify-between text-xs text-slate-500">
                <span>Response Latency (Avg / P95)</span>
                <Activity className="h-4 w-4 text-indigo-600" />
              </div>
              <div className="mt-2 text-2xl font-bold text-slate-900">
                {kpis.avg_latency_ms} ms
              </div>
              <div className="mt-1 text-xs text-slate-500">
                P95 latency: {kpis.p95_latency_ms} ms across {kpis.total_sessions} sessions
              </div>
            </div>

            <div className="rounded-xl border border-slate-200 bg-white p-4 shadow-sm">
              <div className="flex items-center justify-between text-xs text-slate-500">
                <span>RAG Faithfulness / Relevance</span>
                <ShieldCheck className="h-4 w-4 text-emerald-600" />
              </div>
              <div className="mt-2 text-2xl font-bold text-slate-900">
                {Math.round(kpis.avg_faithfulness_score * 100)}% /{" "}
                {Math.round(kpis.avg_relevance_score * 100)}%
              </div>
              <div className="mt-1 text-xs text-slate-500">
                Grounded against corporate SOP chunk corpus
              </div>
            </div>
          </div>

          {/* Guardrail Counter Strip */}
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-3">
            <div className="flex items-center justify-between rounded-xl border border-amber-200 bg-amber-50/70 px-4 py-3">
              <div>
                <div className="text-xs font-medium text-amber-900">PII Scrubbing Triggers</div>
                <div className="text-lg font-bold text-amber-950">
                  {kpis.pii_redactions_count}
                </div>
              </div>
              <ShieldCheck className="h-6 w-6 text-amber-600" />
            </div>

            <div className="flex items-center justify-between rounded-xl border border-rose-200 bg-rose-50/70 px-4 py-3">
              <div>
                <div className="text-xs font-medium text-rose-900">Prompt Injections Blocked</div>
                <div className="text-lg font-bold text-rose-950">
                  {kpis.injections_blocked_count}
                </div>
              </div>
              <ShieldAlert className="h-6 w-6 text-rose-600" />
            </div>

            <div className="flex items-center justify-between rounded-xl border border-slate-200 bg-white px-4 py-3">
              <div>
                <div className="text-xs font-medium text-slate-600">RBAC Access Denials</div>
                <div className="text-lg font-bold text-slate-900">
                  {kpis.rbac_denials_count}
                </div>
              </div>
              <Lock className="h-6 w-6 text-slate-500" />
            </div>
          </div>
        </>
      )}

      {/* Recharts Visualizations */}
      {summary && <KpiCharts data={summary} />}

      {/* Knowledge Base Manager */}
      <div className="rounded-xl border border-slate-200 bg-white p-5 shadow-sm">
        <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
          <div className="flex items-center gap-2">
            <Database className="h-4 w-4 text-blue-600" />
            <h2 className="text-sm font-semibold text-slate-900">
              IT SOP Knowledge Base Manager ({kbDocs.length} Docs · {totalChunks} Hybrid Chunks)
            </h2>
          </div>

          <div className="flex flex-wrap items-center gap-2">
            <label className="inline-flex cursor-pointer items-center gap-1.5 rounded-lg bg-blue-600 px-3 py-1.5 text-xs font-semibold text-white shadow-sm hover:bg-blue-700">
              <FileUp className="h-3.5 w-3.5" />
              <span>{uploading ? "Indexing..." : "Upload SOP (.md/.txt/.pdf)"}</span>
              <input
                type="file"
                accept=".md,.markdown,.txt,.pdf"
                onChange={handleFileUpload}
                disabled={uploading}
                className="hidden"
              />
            </label>

            <button
              type="button"
              onClick={handleReindex}
              disabled={reindexing}
              className="inline-flex items-center gap-1.5 rounded-lg border border-slate-200 bg-slate-50 px-3 py-1.5 text-xs font-semibold text-slate-700 hover:bg-slate-100"
            >
              <RefreshCw className={`h-3.5 w-3.5 ${reindexing ? "animate-spin" : ""}`} />
              <span>{reindexing ? "Re-indexing..." : "Re-index KB"}</span>
            </button>
          </div>
        </div>

        {/* Hybrid Search Tester */}
        <form onSubmit={handleKBSearch} className="mb-4 flex items-center gap-2">
          <input
            type="text"
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            placeholder="Test Hybrid RAG retrieval (Vector + BM25 RRF) e.g. 'VPN-412 BitLocker'..."
            className="flex-1 rounded-lg border border-slate-300 px-3 py-2 text-xs focus:border-blue-500 focus:outline-none"
          />
          <button
            type="submit"
            className="inline-flex items-center gap-1.5 rounded-lg bg-slate-800 px-3 py-2 text-xs font-semibold text-white hover:bg-slate-900"
          >
            <Search className="h-3.5 w-3.5" />
            <span>Test Search</span>
          </button>
        </form>

        {searchResults.length > 0 && (
          <div className="mb-4 space-y-2 rounded-lg border border-blue-200 bg-blue-50/50 p-3 text-xs">
            <div className="font-semibold text-blue-900">
              Top Hybrid Search Results ({searchResults.length})
            </div>
            {searchResults.map((r) => (
              <div key={r.chunk_id} className="rounded bg-white p-2.5 shadow-sm">
                <div className="flex flex-wrap items-center justify-between gap-2 font-semibold text-slate-800">
                  <span>
                    [{r.doc_code}] {r.section}
                  </span>
                  <span className="text-[11px] text-blue-700">
                    Hybrid: {Math.round(r.score * 100)}% (Vec {Math.round(r.vector_score * 100)}% · BM25{" "}
                    {Math.round(r.bm25_score * 100)}%)
                  </span>
                </div>
                <p className="mt-1 text-[11px] text-slate-600">{r.excerpt}</p>
              </div>
            ))}
          </div>
        )}

        <div className="overflow-x-auto">
          <table className="min-w-full divide-y divide-slate-200 text-xs">
            <thead>
              <tr className="text-left font-semibold uppercase tracking-wide text-slate-500">
                <th className="py-2 pr-3">Code</th>
                <th className="py-2 pr-3">Filename</th>
                <th className="py-2 pr-3">Title</th>
                <th className="py-2 pr-3">Chunks</th>
                <th className="py-2 pr-3">Hash</th>
                <th className="py-2 text-right">Actions</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100">
              {kbDocs.map((doc) => (
                <tr key={doc.id} className="hover:bg-slate-50">
                  <td className="py-2 pr-3 font-mono font-semibold text-blue-700">
                    {doc.doc_code}
                  </td>
                  <td className="py-2 pr-3 font-mono text-slate-700">{doc.filename}</td>
                  <td className="py-2 pr-3 text-slate-900">{doc.title}</td>
                  <td className="py-2 pr-3">{doc.chunk_count}</td>
                  <td className="py-2 pr-3 font-mono text-slate-500">{doc.content_hash}</td>
                  <td className="py-2 text-right">
                    <button
                      type="button"
                      onClick={() => handleDeleteDoc(doc.id, doc.filename)}
                      className="inline-flex items-center gap-1 rounded px-2 py-1 text-rose-600 hover:bg-rose-50"
                    >
                      <Trash2 className="h-3.5 w-3.5" />
                      <span>Delete</span>
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      {/* Security Audit Logs & Webhook Ledger */}
      <div className="rounded-xl border border-slate-200 bg-white p-5 shadow-sm">
        <div className="mb-4 flex items-center gap-2 border-b border-slate-200 pb-3">
          <button
            type="button"
            onClick={() => setActiveTab("audit")}
            className={`inline-flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-xs font-semibold ${
              activeTab === "audit"
                ? "bg-blue-50 text-blue-700"
                : "text-slate-600 hover:bg-slate-100"
            }`}
          >
            <ShieldCheck className="h-3.5 w-3.5" />
            <span>Security & Guardrail Audit Logs ({auditLogs.length})</span>
          </button>

          <button
            type="button"
            onClick={() => setActiveTab("webhooks")}
            className={`inline-flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-xs font-semibold ${
              activeTab === "webhooks"
                ? "bg-blue-50 text-blue-700"
                : "text-slate-600 hover:bg-slate-100"
            }`}
          >
            <Webhook className="h-3.5 w-3.5" />
            <span>Webhook Integration Ledger ({webhooks.length})</span>
          </button>
        </div>

        {activeTab === "audit" ? (
          <div className="max-h-96 overflow-y-auto">
            <table className="min-w-full divide-y divide-slate-200 text-xs">
              <thead>
                <tr className="text-left font-semibold uppercase tracking-wide text-slate-500">
                  <th className="py-2 pr-3">Event</th>
                  <th className="py-2 pr-3">Severity</th>
                  <th className="py-2 pr-3">Actor</th>
                  <th className="py-2 pr-3">Action</th>
                  <th className="py-2">Timestamp</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100">
                {auditLogs.map((log) => (
                  <tr key={log.id} className="hover:bg-slate-50">
                    <td className="py-2 pr-3 font-mono font-semibold text-slate-800">
                      {log.event_type}
                    </td>
                    <td className="py-2 pr-3">
                      <span
                        className={`rounded-full px-2 py-0.5 text-[10px] font-semibold uppercase ${
                          log.severity === "high" || log.severity === "critical"
                            ? "bg-rose-100 text-rose-800"
                            : log.severity === "warning"
                            ? "bg-amber-100 text-amber-800"
                            : "bg-slate-100 text-slate-700"
                        }`}
                      >
                        {log.severity}
                      </span>
                    </td>
                    <td className="py-2 pr-3 text-slate-600">
                      {log.actor_email} ({log.actor_role})
                    </td>
                    <td className="py-2 pr-3 text-slate-800">{log.action}</td>
                    <td className="py-2 font-mono text-[11px] text-slate-500">
                      {log.created_at ? new Date(log.created_at).toLocaleString() : "—"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <div className="max-h-96 overflow-y-auto">
            <table className="min-w-full divide-y divide-slate-200 text-xs">
              <thead>
                <tr className="text-left font-semibold uppercase tracking-wide text-slate-500">
                  <th className="py-2 pr-3">Target</th>
                  <th className="py-2 pr-3">Event</th>
                  <th className="py-2 pr-3">Mode</th>
                  <th className="py-2 pr-3">HTTP Status</th>
                  <th className="py-2 pr-3">External Ref</th>
                  <th className="py-2">Latency</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100">
                {webhooks.map((w) => (
                  <tr key={w.id} className="hover:bg-slate-50">
                    <td className="py-2 pr-3 font-mono font-semibold uppercase text-blue-700">
                      {w.target_system}
                    </td>
                    <td className="py-2 pr-3 font-mono text-slate-800">{w.event_name}</td>
                    <td className="py-2 pr-3">
                      <span className="rounded bg-slate-100 px-2 py-0.5 text-[10px] font-semibold uppercase text-slate-700">
                        {w.delivery_mode}
                      </span>
                    </td>
                    <td className="py-2 pr-3 font-mono text-emerald-700">
                      {w.response_status}
                    </td>
                    <td className="py-2 pr-3 font-mono text-slate-600">
                      {String(w.response_body?.external_ref || "—")}
                    </td>
                    <td className="py-2 font-mono text-slate-500">{w.latency_ms} ms</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
}
