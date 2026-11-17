"use client";

import React from "react";
import {
  Area,
  AreaChart,
  Bar,
  BarChart,
  CartesianGrid,
  Legend,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { AnalyticsSummary } from "@/lib/api";

export function KpiCharts({ data }: { data: AnalyticsSummary }) {
  const { performance_timeseries, intent_breakdown } = data;

  return (
    <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
      {/* Latency & RAG Quality Trend */}
      <div className="rounded-xl border border-slate-200 bg-white p-5 shadow-sm">
        <div className="mb-4">
          <h3 className="text-sm font-semibold text-slate-900">
            RAG Faithfulness & Relevance Trend (%)
          </h3>
          <p className="text-xs text-slate-500">
            Recent conversational turns evaluated against grounded SOP contexts
          </p>
        </div>
        <div className="h-64 w-full">
          <ResponsiveContainer width="100%" height="100%">
            <LineChart data={performance_timeseries}>
              <CartesianGrid strokeDasharray="3 3" stroke="#e2e8f0" />
              <XAxis dataKey="label" tick={{ fontSize: 11, fill: "#64748b" }} />
              <YAxis domain={[0, 100]} tick={{ fontSize: 11, fill: "#64748b" }} />
              <Tooltip />
              <Legend wrapperStyle={{ fontSize: 12 }} />
              <Line
                type="monotone"
                dataKey="faithfulness"
                name="Faithfulness (%)"
                stroke="#2563eb"
                strokeWidth={2.2}
                dot={{ r: 3 }}
                connectNulls
              />
              <Line
                type="monotone"
                dataKey="relevance"
                name="Relevance (%)"
                stroke="#10b981"
                strokeWidth={2.2}
                dot={{ r: 3 }}
                connectNulls
              />
            </LineChart>
          </ResponsiveContainer>
        </div>
      </div>

      {/* Response Latency Trend */}
      <div className="rounded-xl border border-slate-200 bg-white p-5 shadow-sm">
        <div className="mb-4">
          <h3 className="text-sm font-semibold text-slate-900">
            End-to-End Response Latency (ms)
          </h3>
          <p className="text-xs text-slate-500">
            Hybrid retrieval + guardrail + orchestration processing duration per turn
          </p>
        </div>
        <div className="h-64 w-full">
          <ResponsiveContainer width="100%" height="100%">
            <AreaChart data={performance_timeseries}>
              <CartesianGrid strokeDasharray="3 3" stroke="#e2e8f0" />
              <XAxis dataKey="label" tick={{ fontSize: 11, fill: "#64748b" }} />
              <YAxis tick={{ fontSize: 11, fill: "#64748b" }} />
              <Tooltip />
              <Area
                type="monotone"
                dataKey="latency_ms"
                name="Latency (ms)"
                stroke="#6366f1"
                fill="#e0e7ff"
                strokeWidth={2}
              />
            </AreaChart>
          </ResponsiveContainer>
        </div>
      </div>

      {/* Intent & Workflow Distribution */}
      <div className="rounded-xl border border-slate-200 bg-white p-5 shadow-sm lg:col-span-2">
        <div className="mb-4">
          <h3 className="text-sm font-semibold text-slate-900">
            Helpdesk Intent & Automation Workflow Breakdown
          </h3>
          <p className="text-xs text-slate-500">
            Distribution of RAG Q&A, password resets, software provisioning, escalations, and blocked threats
          </p>
        </div>
        <div className="h-56 w-full">
          <ResponsiveContainer width="100%" height="100%">
            <BarChart data={intent_breakdown}>
              <CartesianGrid strokeDasharray="3 3" stroke="#e2e8f0" />
              <XAxis dataKey="intent" tick={{ fontSize: 11, fill: "#475569" }} />
              <YAxis allowDecimals={false} tick={{ fontSize: 11, fill: "#64748b" }} />
              <Tooltip />
              <Bar
                dataKey="count"
                name="Turn Count"
                fill="#2563eb"
                radius={[6, 6, 0, 0]}
              />
            </BarChart>
          </ResponsiveContainer>
        </div>
      </div>
    </div>
  );
}
