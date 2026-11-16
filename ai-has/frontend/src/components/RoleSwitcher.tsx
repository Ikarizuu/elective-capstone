"use client";

import React, { createContext, useContext, useEffect, useState } from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import {
  Activity,
  Bot,
  Headphones,
  ShieldCheck,
  UserCheck,
} from "lucide-react";
import {
  fetchHealth,
  HealthStatus,
  Persona,
  PERSONAS,
  UserRole,
} from "@/lib/api";

interface RoleContextValue {
  role: UserRole;
  persona: Persona;
  setRole: (role: UserRole) => void;
  health: HealthStatus | null;
}

const RoleContext = createContext<RoleContextValue>({
  role: "employee",
  persona: PERSONAS.employee,
  setRole: () => {},
  health: null,
});

export function RoleProvider({ children }: { children: React.ReactNode }) {
  const [role, setRoleState] = useState<UserRole>("employee");
  const [health, setHealth] = useState<HealthStatus | null>(null);

  useEffect(() => {
    const saved = window.localStorage.getItem("ai_has_role") as UserRole | null;
    if (saved && saved in PERSONAS) {
      setRoleState(saved);
    }
    const loadHealth = async () => {
      try {
        const h = await fetchHealth();
        setHealth(h);
      } catch {
        setHealth(null);
      }
    };
    loadHealth();
    const timer = setInterval(loadHealth, 15000);
    return () => clearInterval(timer);
  }, []);

  const setRole = (next: UserRole) => {
    setRoleState(next);
    window.localStorage.setItem("ai_has_role", next);
  };

  return (
    <RoleContext.Provider
      value={{ role, persona: PERSONAS[role], setRole, health }}
    >
      {children}
    </RoleContext.Provider>
  );
}

export function useRole() {
  return useContext(RoleContext);
}

export function TopNavBar() {
  const pathname = usePathname();
  const { role, persona, setRole, health } = useRole();

  const navItems = [
    { href: "/", label: "Employee Portal", icon: Bot },
    { href: "/agent", label: "Support Agent Queue", icon: Headphones },
    { href: "/admin", label: "Admin Observability", icon: Activity },
  ];

  return (
    <header className="sticky top-0 z-30 border-b border-slate-200 bg-white/95 backdrop-blur">
      <div className="mx-auto flex max-w-7xl flex-wrap items-center justify-between gap-3 px-4 py-3 sm:px-6 lg:px-8">
        <div className="flex items-center gap-6">
          <Link href="/" className="flex items-center gap-2.5">
            <div className="flex h-9 w-9 items-center justify-center rounded-lg bg-blue-600 text-white shadow-sm">
              <ShieldCheck className="h-5 w-5" />
            </div>
            <div>
              <div className="flex items-center gap-2">
                <span className="text-base font-bold tracking-tight text-slate-900">
                  AI-HAS
                </span>
                <span className="rounded bg-blue-50 px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-blue-700">
                  Enterprise
                </span>
              </div>
              <p className="text-[11px] text-slate-500">
                Helpdesk & Workflow Automation
              </p>
            </div>
          </Link>

          <nav className="flex items-center gap-1">
            {navItems.map((item) => {
              const Icon = item.icon;
              const active = pathname === item.href;
              return (
                <Link
                  key={item.href}
                  href={item.href}
                  className={`flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-sm font-medium transition-colors ${
                    active
                      ? "bg-blue-50 text-blue-700"
                      : "text-slate-600 hover:bg-slate-100 hover:text-slate-900"
                  }`}
                >
                  <Icon className="h-4 w-4" />
                  <span>{item.label}</span>
                </Link>
              );
            })}
          </nav>
        </div>

        <div className="flex items-center gap-3">
          <div className="hidden items-center gap-2 rounded-full border border-slate-200 bg-slate-50 px-3 py-1 text-xs md:flex">
            <span
              className={`h-2 w-2 rounded-full ${
                health ? "bg-emerald-500" : "bg-rose-500"
              }`}
            />
            {health ? (
              <span className="text-slate-600">
                {health.openai_configured ? health.chat_model : "SOP Deterministic Engine"}{" "}
                · {health.kb_documents} SOPs ({health.kb_chunks} chunks)
              </span>
            ) : (
              <span className="text-rose-600">API Offline</span>
            )}
          </div>

          <div className="flex items-center gap-2 rounded-lg border border-slate-200 bg-white px-2.5 py-1.5 shadow-sm">
            <UserCheck className="h-4 w-4 text-blue-600" />
            <div className="text-xs leading-tight">
              <div className="font-semibold text-slate-800">{persona.fullName}</div>
              <div className="text-[10px] text-slate-500">{persona.email}</div>
            </div>
            <select
              aria-label="Switch RBAC Role"
              value={role}
              onChange={(e) => setRole(e.target.value as UserRole)}
              className="ml-1 rounded-md border border-slate-200 bg-slate-50 px-2 py-1 text-xs font-medium text-slate-800 focus:border-blue-500 focus:outline-none"
            >
              <option value="employee">Role: Employee</option>
              <option value="support_agent">Role: Support Agent</option>
              <option value="admin">Role: Admin</option>
            </select>
          </div>
        </div>
      </div>
    </header>
  );
}
