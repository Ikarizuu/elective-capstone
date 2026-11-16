"use client";

import React, { useCallback, useEffect, useState } from "react";
import {
  AlertTriangle,
  Bot,
  CheckCircle2,
  Headphones,
  RefreshCw,
  Send,
  ShieldAlert,
  Sparkles,
  User,
  UserCheck,
} from "lucide-react";
import {
  ApiError,
  ChatSession,
  listEscalatedQueue,
  listTickets,
  resolveSession,
  sendAgentMessage,
  takeoverSession,
  Ticket,
  updateTicket,
} from "@/lib/api";
import { useRole } from "@/components/RoleSwitcher";

export default function AgentConsolePage() {
  const { role, persona, setRole } = useRole();
  const [queue, setQueue] = useState<ChatSession[]>([]);
  const [tickets, setTickets] = useState<Ticket[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [replyText, setReplyText] = useState("");
  const [resolutionNotes, setResolutionNotes] = useState("");
  const [rbacError, setRbacError] = useState<string | null>(null);
  const [actionMsg, setActionMsg] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  const loadConsole = useCallback(async () => {
    setLoading(true);
    try {
      const [qRes, tRes] = await Promise.all([
        listEscalatedQueue(persona),
        listTickets(persona),
      ]);
      setRbacError(null);
      const items = qRes.queue || [];
      setQueue(items);
      setTickets(tRes.tickets || []);
      setSelectedId((prev) =>
        prev && items.some((i) => i.id === prev) ? prev : items[0]?.id || null
      );
    } catch (err) {
      if (err instanceof ApiError && err.status === 403) {
        setRbacError(err.message);
      }
    } finally {
      setLoading(false);
    }
  }, [persona]);

  useEffect(() => {
    loadConsole();
    const timer = setInterval(loadConsole, 5000);
    return () => clearInterval(timer);
  }, [loadConsole]);

  const selectedSession = queue.find((s) => s.id === selectedId) || null;

  const handleTakeover = async (sessionId: string) => {
    try {
      await takeoverSession(persona, sessionId);
      setActionMsg("Took over live conversation from AI.");
      await loadConsole();
    } catch (err) {
      setActionMsg(err instanceof Error ? err.message : "Takeover failed");
    }
  };

  const handleSendReply = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!selectedSession || !replyText.trim()) return;
    try {
      await sendAgentMessage(persona, selectedSession.id, replyText.trim());
      setReplyText("");
      await loadConsole();
    } catch (err) {
      setActionMsg(err instanceof Error ? err.message : "Failed to send message");
    }
  };

  const handleResolveSession = async () => {
    if (!selectedSession) return;
    const notes =
      resolutionNotes.trim() ||
      `Issue verified and resolved by ${persona.fullName}.`;
    try {
      const res = await resolveSession(persona, selectedSession.id, notes);
      setResolutionNotes("");
      setActionMsg(
        `Session resolved & closed (MTTR: ${res.mttr_minutes} min).`
      );
      await loadConsole();
    } catch (err) {
      setActionMsg(err instanceof Error ? err.message : "Resolution failed");
    }
  };

  const handleTicketStatusChange = async (ticketId: string, status: string) => {
    try {
      await updateTicket(persona, ticketId, {
        status,
        assigned_agent_email: persona.email,
      });
      await loadConsole();
    } catch (err) {
      setActionMsg(err instanceof Error ? err.message : "Ticket update failed");
    }
  };

  if (rbacError) {
    return (
      <div className="mx-auto max-w-xl rounded-xl border border-amber-200 bg-amber-50 p-6 text-center shadow-sm">
        <ShieldAlert className="mx-auto h-10 w-10 text-amber-600" />
        <h1 className="mt-3 text-lg font-bold text-slate-900">
          RBAC Protection: Support Agent Console
        </h1>
        <p className="mt-2 text-sm text-slate-700">{rbacError}</p>
        <p className="mt-1 text-xs text-slate-500">
          Current active role: <span className="font-mono font-semibold">{role}</span>
        </p>
        <button
          type="button"
          onClick={() => setRole("support_agent")}
          className="mt-4 inline-flex items-center gap-2 rounded-lg bg-blue-600 px-4 py-2 text-sm font-semibold text-white hover:bg-blue-700"
        >
          <UserCheck className="h-4 w-4" />
          <span>Switch to Support Agent (Maya Patel)</span>
        </button>
      </div>
    );
  }

  return (
    <div className="space-y-6">
      {/* Header Bar */}
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-xl font-bold text-slate-900">
            Human-in-the-Loop Live Queue & Takeover Console
          </h1>
          <p className="text-xs text-slate-500">
            Review AI-generated handoff summaries, assume live chat control, and manage ITSM tickets.
          </p>
        </div>

        <button
          type="button"
          onClick={loadConsole}
          className="inline-flex items-center gap-1.5 rounded-lg border border-slate-200 bg-white px-3 py-1.5 text-xs font-semibold text-slate-700 shadow-sm hover:bg-slate-50"
        >
          <RefreshCw className={`h-3.5 w-3.5 ${loading ? "animate-spin" : ""}`} />
          <span>Refresh Queue ({queue.length})</span>
        </button>
      </div>

      {actionMsg && (
        <div className="flex items-center justify-between rounded-lg border border-emerald-200 bg-emerald-50 px-4 py-2 text-xs font-medium text-emerald-800">
          <span>{actionMsg}</span>
          <button type="button" onClick={() => setActionMsg(null)} className="underline">
            Dismiss
          </button>
        </div>
      )}

      {/* Main Split: Escalation Queue + Live Conversation */}
      <div className="grid grid-cols-1 gap-6 lg:grid-cols-12">
        {/* Queue List */}
        <div className="rounded-xl border border-slate-200 bg-white p-4 shadow-sm lg:col-span-4">
          <h2 className="mb-3 flex items-center gap-2 text-sm font-semibold text-slate-900">
            <Headphones className="h-4 w-4 text-amber-600" />
            <span>Escalated & Live Sessions ({queue.length})</span>
          </h2>

          <div className="max-h-[540px] space-y-2 overflow-y-auto pr-1">
            {queue.length === 0 ? (
              <div className="rounded-lg border border-dashed border-slate-200 p-6 text-center text-xs text-slate-400">
                Zero sessions waiting in the escalation queue.
              </div>
            ) : (
              queue.map((item) => {
                const selected = item.id === selectedId;
                return (
                  <button
                    key={item.id}
                    type="button"
                    onClick={() => setSelectedId(item.id)}
                    className={`w-full rounded-lg border p-3 text-left transition-colors ${
                      selected
                        ? "border-blue-400 bg-blue-50/70"
                        : "border-slate-200 bg-slate-50/50 hover:bg-slate-100"
                    }`}
                  >
                    <div className="flex items-center justify-between gap-2">
                      <span className="truncate text-xs font-semibold text-slate-900">
                        {item.user_email}
                      </span>
                      <span
                        className={`rounded-full px-2 py-0.5 text-[10px] font-semibold uppercase ${
                          item.status === "agent_active"
                            ? "bg-emerald-100 text-emerald-800"
                            : "bg-amber-100 text-amber-800"
                        }`}
                      >
                        {item.status === "agent_active" ? "Live Agent" : "Waiting"}
                      </span>
                    </div>
                    <p className="mt-1 line-clamp-2 text-xs text-slate-700">
                      {item.title}
                    </p>
                    {item.ticket && (
                      <div className="mt-2 flex items-center justify-between text-[11px]">
                        <span className="font-mono font-semibold text-blue-700">
                          {item.ticket.id}
                        </span>
                        <span className="rounded bg-rose-100 px-1.5 py-0.5 text-[10px] font-semibold uppercase text-rose-800">
                          {item.ticket.priority}
                        </span>
                      </div>
                    )}
                  </button>
                );
              })
            )}
          </div>
        </div>

        {/* Selected Escalation Workspace */}
        <div className="rounded-xl border border-slate-200 bg-white shadow-sm lg:col-span-8">
          {!selectedSession ? (
            <div className="flex h-[540px] items-center justify-center p-8 text-center text-sm text-slate-400">
              Select an escalated conversation on the left to inspect the AI handoff summary and take over live.
            </div>
          ) : (
            <div className="flex h-[600px] flex-col">
              {/* Session Header + Takeover CTA */}
              <div className="flex flex-wrap items-center justify-between gap-3 border-b border-slate-200 px-4 py-3">
                <div>
                  <div className="flex items-center gap-2">
                    <h3 className="text-sm font-bold text-slate-900">
                      {selectedSession.user_email}
                    </h3>
                    {selectedSession.ticket && (
                      <span className="rounded bg-blue-50 px-2 py-0.5 font-mono text-xs font-semibold text-blue-700">
                        {selectedSession.ticket.id}
                      </span>
                    )}
                  </div>
                  <p className="text-xs text-slate-500">
                    Session ID: <span className="font-mono">{selectedSession.id}</span>
                  </p>
                </div>

                <div className="flex items-center gap-2">
                  {selectedSession.status === "escalated" && (
                    <button
                      type="button"
                      onClick={() => handleTakeover(selectedSession.id)}
                      className="inline-flex items-center gap-1.5 rounded-lg bg-blue-600 px-3 py-1.5 text-xs font-semibold text-white shadow-sm hover:bg-blue-700"
                    >
                      <Headphones className="h-3.5 w-3.5" />
                      <span>Take Over Live Chat</span>
                    </button>
                  )}
                  <button
                    type="button"
                    onClick={handleResolveSession}
                    className="inline-flex items-center gap-1.5 rounded-lg bg-emerald-600 px-3 py-1.5 text-xs font-semibold text-white shadow-sm hover:bg-emerald-700"
                  >
                    <CheckCircle2 className="h-3.5 w-3.5" />
                    <span>Resolve & Close</span>
                  </button>
                </div>
              </div>

              {/* AI Handoff Summary Banner */}
              {selectedSession.handoff_summary && (
                <div className="border-b border-amber-200 bg-amber-50/80 px-4 py-3 text-xs text-amber-950">
                  <div className="mb-1 flex items-center gap-1.5 font-semibold text-amber-900">
                    <Sparkles className="h-3.5 w-3.5 text-amber-600" />
                    <span>AI-Generated Handoff Summary</span>
                  </div>
                  <p className="leading-relaxed">{selectedSession.handoff_summary}</p>
                </div>
              )}

              {/* Transcript */}
              <div className="flex-1 space-y-3 overflow-y-auto p-4">
                {(selectedSession.messages || []).map((msg) => {
                  const isAgent = msg.sender_role === "support_agent";
                  const isUser = msg.sender_role === "user";
                  const isSystem = msg.sender_role === "system";

                  if (isSystem) {
                    return (
                      <div key={msg.id} className="text-center">
                        <span className="rounded-full bg-slate-100 px-3 py-1 text-[11px] text-slate-600">
                          {msg.content}
                        </span>
                      </div>
                    );
                  }

                  return (
                    <div
                      key={msg.id}
                      className={`flex gap-2.5 ${isAgent ? "justify-end" : "justify-start"}`}
                    >
                      {!isAgent && (
                        <div className="mt-1 flex h-7 w-7 shrink-0 items-center justify-center rounded-lg bg-slate-200 text-slate-700">
                          {isUser ? <User className="h-3.5 w-3.5" /> : <Bot className="h-3.5 w-3.5 text-blue-600" />}
                        </div>
                      )}
                      <div
                        className={`max-w-[80%] rounded-xl px-3.5 py-2.5 text-xs ${
                          isAgent
                            ? "bg-emerald-600 text-white"
                            : isUser
                            ? "border border-slate-200 bg-slate-100 text-slate-900"
                            : "border border-blue-200 bg-blue-50 text-slate-900"
                        }`}
                      >
                        <div className="mb-1 font-semibold opacity-80">
                          {isAgent
                            ? `You (${msg.sender_email})`
                            : isUser
                            ? selectedSession.user_email
                            : "AI-HAS Assistant"}
                        </div>
                        <div className="whitespace-pre-wrap leading-relaxed">{msg.content}</div>
                      </div>
                    </div>
                  );
                })}
              </div>

              {/* Resolution Notes + Live Agent Reply Composer */}
              <div className="space-y-2 border-t border-slate-200 bg-slate-50 p-3">
                <input
                  type="text"
                  value={resolutionNotes}
                  onChange={(e) => setResolutionNotes(e.target.value)}
                  placeholder="Optional resolution notes (used when clicking Resolve & Close)..."
                  className="w-full rounded-md border border-slate-200 bg-white px-3 py-1.5 text-xs text-slate-700 focus:border-emerald-500 focus:outline-none"
                />
                <form onSubmit={handleSendReply} className="flex items-center gap-2">
                  <input
                    type="text"
                    value={replyText}
                    onChange={(e) => setReplyText(e.target.value)}
                    placeholder="Send a live response directly to the employee..."
                    className="flex-1 rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm focus:border-blue-500 focus:outline-none"
                  />
                  <button
                    type="submit"
                    disabled={!replyText.trim()}
                    className="inline-flex items-center gap-1.5 rounded-lg bg-emerald-600 px-4 py-2 text-sm font-semibold text-white hover:bg-emerald-700 disabled:opacity-50"
                  >
                    <Send className="h-4 w-4" />
                    <span>Send Live Reply</span>
                  </button>
                </form>
              </div>
            </div>
          )}
        </div>
      </div>

      {/* ITSM Ticket Queue Table */}
      <div className="rounded-xl border border-slate-200 bg-white p-5 shadow-sm">
        <div className="mb-4 flex items-center justify-between">
          <div className="flex items-center gap-2">
            <AlertTriangle className="h-4 w-4 text-blue-600" />
            <h2 className="text-sm font-semibold text-slate-900">
              Enterprise ITSM Ticket Queue ({tickets.length})
            </h2>
          </div>
        </div>

        <div className="overflow-x-auto">
          <table className="min-w-full divide-y divide-slate-200 text-xs">
            <thead>
              <tr className="text-left font-semibold uppercase tracking-wide text-slate-500">
                <th className="py-2 pr-3">Ticket ID</th>
                <th className="py-2 pr-3">Title</th>
                <th className="py-2 pr-3">Requester</th>
                <th className="py-2 pr-3">Category</th>
                <th className="py-2 pr-3">Priority</th>
                <th className="py-2 pr-3">External Ref</th>
                <th className="py-2">Status</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100">
              {tickets.map((t) => (
                <tr key={t.id} className="hover:bg-slate-50">
                  <td className="py-2.5 pr-3 font-mono font-semibold text-blue-700">
                    {t.id}
                  </td>
                  <td className="max-w-xs py-2.5 pr-3">
                    <div className="font-medium text-slate-900">{t.title}</div>
                    {t.ai_handoff_summary && (
                      <div className="mt-0.5 line-clamp-1 text-[11px] text-slate-500">
                        {t.ai_handoff_summary}
                      </div>
                    )}
                  </td>
                  <td className="py-2.5 pr-3 text-slate-600">{t.requester_email}</td>
                  <td className="py-2.5 pr-3 font-mono text-slate-600">{t.category}</td>
                  <td className="py-2.5 pr-3">
                    <span
                      className={`rounded-full px-2 py-0.5 text-[10px] font-semibold uppercase ${
                        t.priority === "critical" || t.priority === "high"
                          ? "bg-rose-100 text-rose-800"
                          : "bg-slate-100 text-slate-700"
                      }`}
                    >
                      {t.priority}
                    </span>
                  </td>
                  <td className="py-2.5 pr-3 font-mono text-slate-500">
                    {t.external_ticket_ref || "—"}
                  </td>
                  <td className="py-2.5">
                    <select
                      value={t.status}
                      onChange={(e) => handleTicketStatusChange(t.id, e.target.value)}
                      className="rounded-md border border-slate-200 bg-white px-2 py-1 text-xs font-medium text-slate-800 focus:border-blue-500 focus:outline-none"
                    >
                      <option value="open">Open</option>
                      <option value="in_progress">In Progress</option>
                      <option value="escalated">Escalated</option>
                      <option value="pending_approval">Pending Approval</option>
                      <option value="resolved">Resolved</option>
                      <option value="closed">Closed</option>
                    </select>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}
