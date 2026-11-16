"use client";

import React, { useCallback, useEffect, useState } from "react";
import {
  MessageSquarePlus,
  MessagesSquare,
  RefreshCw,
  Ticket as TicketIcon,
} from "lucide-react";
import {
  ChatSession,
  listSessions,
  listTickets,
  Ticket,
} from "@/lib/api";
import { useRole } from "@/components/RoleSwitcher";
import { ChatWidget } from "@/components/ChatWidget";

export default function EmployeePortalPage() {
  const { persona } = useRole();
  const [sessions, setSessions] = useState<ChatSession[]>([]);
  const [tickets, setTickets] = useState<Ticket[]>([]);
  const [activeSessionId, setActiveSessionId] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  const loadSidebarData = useCallback(async () => {
    setLoading(true);
    try {
      const [sessRes, tktRes] = await Promise.all([
        listSessions(persona),
        listTickets(persona),
      ]);
      setSessions(sessRes.sessions || []);
      setTickets(tktRes.tickets || []);
    } catch {
      // handled gracefully when backend is starting
    } finally {
      setLoading(false);
    }
  }, [persona]);

  useEffect(() => {
    setActiveSessionId(null);
    loadSidebarData();
  }, [persona, loadSidebarData]);

  return (
    <div className="grid grid-cols-1 gap-6 lg:grid-cols-12">
      {/* Left Sidebar: Sessions & Employee Tickets */}
      <aside className="space-y-5 lg:col-span-4">
        <div className="rounded-xl border border-slate-200 bg-white p-4 shadow-sm">
          <div className="mb-3 flex items-center justify-between">
            <div className="flex items-center gap-2 text-sm font-semibold text-slate-900">
              <MessagesSquare className="h-4 w-4 text-blue-600" />
              <span>Support Conversations</span>
            </div>
            <button
              type="button"
              onClick={loadSidebarData}
              title="Refresh sessions and tickets"
              className="rounded-md p-1 text-slate-500 hover:bg-slate-100 hover:text-slate-800"
            >
              <RefreshCw className={`h-3.5 w-3.5 ${loading ? "animate-spin" : ""}`} />
            </button>
          </div>

          <button
            type="button"
            onClick={() => setActiveSessionId(null)}
            className="mb-3 flex w-full items-center justify-center gap-2 rounded-lg bg-blue-600 px-3 py-2 text-xs font-semibold text-white shadow-sm transition-colors hover:bg-blue-700"
          >
            <MessageSquarePlus className="h-4 w-4" />
            <span>New Support Chat</span>
          </button>

          <div className="max-h-60 space-y-1.5 overflow-y-auto pr-1">
            {sessions.length === 0 ? (
              <p className="py-4 text-center text-xs text-slate-400">
                No previous conversations yet.
              </p>
            ) : (
              sessions.map((sess) => {
                const selected = sess.id === activeSessionId;
                return (
                  <button
                    key={sess.id}
                    type="button"
                    onClick={() => setActiveSessionId(sess.id)}
                    className={`w-full rounded-lg border px-3 py-2 text-left transition-colors ${
                      selected
                        ? "border-blue-300 bg-blue-50"
                        : "border-slate-200/70 bg-slate-50/50 hover:bg-slate-100"
                    }`}
                  >
                    <div className="flex items-center justify-between gap-2">
                      <span className="truncate text-xs font-semibold text-slate-800">
                        {sess.title}
                      </span>
                      <span
                        className={`shrink-0 rounded px-1.5 py-0.5 text-[10px] font-semibold uppercase ${
                          sess.status === "agent_active"
                            ? "bg-emerald-100 text-emerald-700"
                            : sess.status === "escalated"
                            ? "bg-amber-100 text-amber-800"
                            : sess.status === "closed"
                            ? "bg-slate-200 text-slate-600"
                            : "bg-blue-100 text-blue-700"
                        }`}
                      >
                        {sess.status.replace("_", " ")}
                      </span>
                    </div>
                    <div className="mt-1 text-[11px] text-slate-500">
                      {sess.message_count} messages
                    </div>
                  </button>
                );
              })
            )}
          </div>
        </div>

        <div className="rounded-xl border border-slate-200 bg-white p-4 shadow-sm">
          <div className="mb-3 flex items-center justify-between">
            <div className="flex items-center gap-2 text-sm font-semibold text-slate-900">
              <TicketIcon className="h-4 w-4 text-blue-600" />
              <span>My IT Tickets ({tickets.length})</span>
            </div>
          </div>

          <div className="max-h-72 space-y-2 overflow-y-auto pr-1">
            {tickets.length === 0 ? (
              <p className="py-4 text-center text-xs text-slate-400">
                No tickets logged yet.
              </p>
            ) : (
              tickets.map((t) => (
                <div
                  key={t.id}
                  className="rounded-lg border border-slate-200/80 bg-slate-50 p-2.5 text-xs"
                >
                  <div className="flex items-center justify-between gap-2">
                    <span className="font-mono font-semibold text-blue-700">
                      {t.id}
                    </span>
                    <span
                      className={`rounded-full px-2 py-0.5 text-[10px] font-semibold uppercase ${
                        t.status === "resolved" || t.status === "closed"
                          ? "bg-emerald-100 text-emerald-800"
                          : t.status === "escalated"
                          ? "bg-rose-100 text-rose-800"
                          : t.status === "pending_approval"
                          ? "bg-amber-100 text-amber-800"
                          : "bg-blue-100 text-blue-800"
                      }`}
                    >
                      {t.status.replace("_", " ")}
                    </span>
                  </div>
                  <div className="mt-1 font-medium text-slate-800 line-clamp-2">
                    {t.title}
                  </div>
                  <div className="mt-1 flex items-center justify-between text-[11px] text-slate-500">
                    <span>{t.category}</span>
                    <span className="uppercase">{t.priority}</span>
                  </div>
                </div>
              ))
            )}
          </div>
        </div>
      </aside>

      {/* Main Column: Live Streaming Conversational Helpdesk */}
      <section className="lg:col-span-8">
        <ChatWidget
          sessionId={activeSessionId}
          onSessionCreated={(newId) => {
            setActiveSessionId(newId);
            loadSidebarData();
          }}
          onTurnCompleted={loadSidebarData}
        />
      </section>
    </div>
  );
}
