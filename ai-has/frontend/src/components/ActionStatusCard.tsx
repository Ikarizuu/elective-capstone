"use client";

import React from "react";
import {
  AlertTriangle,
  CheckCircle2,
  Clock,
  KeyRound,
  PackageCheck,
  ShieldAlert,
  Ticket as TicketIcon,
  UserPlus,
} from "lucide-react";
import { ToolCallEvent } from "@/lib/api";

export function ActionStatusCardList({ tools }: { tools: ToolCallEvent[] }) {
  if (!tools || tools.length === 0) return null;

  return (
    <div className="mt-3 space-y-2">
      {tools.map((tool, idx) => {
        const res = tool.result || {};
        const status = String(res.status || tool.status || "executed");
        const actionType = String(res.action_type || tool.tool_name);

        const isDenied = status === "denied" || status === "error";
        const isPendingOrEscalated =
          status === "pending_approval" ||
          status === "escalated" ||
          status === "escalation_required";

        const badgeColor = isDenied
          ? "border-rose-200 bg-rose-50 text-rose-800"
          : isPendingOrEscalated
          ? "border-amber-200 bg-amber-50 text-amber-800"
          : "border-emerald-200 bg-emerald-50 text-emerald-800";

        let Icon = CheckCircle2;
        if (actionType === "password_reset") Icon = KeyRound;
        else if (actionType === "software_provisioning") Icon = PackageCheck;
        else if (actionType === "ticket_created") Icon = TicketIcon;
        else if (actionType === "human_escalation") Icon = UserPlus;
        if (isDenied) Icon = ShieldAlert;
        else if (isPendingOrEscalated) Icon = Clock;

        return (
          <div
            key={`${tool.tool_name}-${idx}`}
            className={`rounded-lg border p-3 text-xs ${badgeColor}`}
          >
            <div className="flex flex-wrap items-center justify-between gap-2">
              <div className="flex items-center gap-2 font-semibold">
                <Icon className="h-4 w-4 shrink-0" />
                <span className="uppercase tracking-wide">
                  {actionType.replace(/_/g, " ")}
                </span>
              </div>
              <span className="rounded-full bg-white/80 px-2 py-0.5 text-[11px] font-semibold uppercase">
                {status.replace(/_/g, " ")}
              </span>
            </div>

            <div className="mt-2 grid grid-cols-1 gap-1.5 text-[11px] sm:grid-cols-2">
              {Boolean(res.ticket_id) && (
                <div>
                  <span className="font-medium opacity-75">Ticket ID:</span>{" "}
                  <span className="font-mono font-semibold">{String(res.ticket_id)}</span>
                </div>
              )}
              {Boolean(res.external_ref) && (
                <div>
                  <span className="font-medium opacity-75">ITSM / Webhook Ref:</span>{" "}
                  <span className="font-mono font-semibold">
                    {String(res.external_ref)} ({String(res.delivery_mode || "sandbox")})
                  </span>
                </div>
              )}
              {Boolean(res.software) && (
                <div>
                  <span className="font-medium opacity-75">Software:</span>{" "}
                  <span className="font-semibold">
                    {String(res.software)} ({String(res.access_level || "standard")})
                  </span>
                </div>
              )}
              {Boolean(res.tier) && (
                <div>
                  <span className="font-medium opacity-75">Policy Tier:</span>{" "}
                  <span className="font-semibold">{String(res.tier)}</span>
                </div>
              )}
              {Boolean(res.target_email) && (
                <div>
                  <span className="font-medium opacity-75">Target Account:</span>{" "}
                  <span className="font-mono font-semibold">{String(res.target_email)}</span>
                </div>
              )}
              {Boolean(res.sla) && (
                <div>
                  <span className="font-medium opacity-75">Approval SLA:</span>{" "}
                  <span className="font-semibold">{String(res.sla)}</span>
                </div>
              )}
              {Boolean(res.expires_in_minutes) && (
                <div>
                  <span className="font-medium opacity-75">Token Expiry:</span>{" "}
                  <span className="font-semibold">{String(res.expires_in_minutes)} minutes</span>
                </div>
              )}
            </div>

            {Boolean(res.reason) && (
              <div className="mt-2 flex items-start gap-1.5 rounded bg-white/70 p-2 text-[11px]">
                <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
                <span>{String(res.reason)}</span>
              </div>
            )}

            {Boolean(res.handoff_summary) && (
              <div className="mt-2 rounded bg-white/75 p-2 text-[11px] leading-relaxed">
                <span className="font-semibold">AI Handoff Summary: </span>
                {String(res.handoff_summary)}
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}
