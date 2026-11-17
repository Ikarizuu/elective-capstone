"use client";

import React, {
  createContext,
  useContext,
  useEffect,
  useRef,
  useState,
} from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import {
  Activity,
  Bot,
  Check,
  ChevronDown,
  Headphones,
  ShieldCheck,
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

const NAV_ITEMS = [
  { href: "/", label: "Helpdesk", icon: Bot },
  { href: "/agent", label: "Agent Queue", icon: Headphones },
  { href: "/admin", label: "Admin", icon: Activity },
];

const ROLE_AVATAR: Record<UserRole, string> = {
  employee: "bg-blue-600",
  support_agent: "bg-violet-600",
  admin: "bg-slate-800",
};

const ROLE_ORDER: UserRole[] = ["employee", "support_agent", "admin"];

function initials(name: string) {
  return name
    .split(" ")
    .map((part) => part[0])
    .join("")
    .slice(0, 2)
    .toUpperCase();
}

function StatusIndicator({ health }: { health: HealthStatus | null }) {
  const online = health !== null;
  const label = !online
    ? "API offline"
    : health.openai_configured
      ? health.chat_model
      : "Local engine";
  const detail = online
    ? `Backend online · ${health.kb_documents} SOPs, ${health.kb_chunks} chunks indexed`
    : "Backend unreachable. Start the API on port 8000.";

  return (
    <div
      className="flex items-center gap-2 text-xs font-medium"
      title={detail}
      role="status"
    >
      <span className="relative flex h-2 w-2" aria-hidden="true">
        {online && (
          <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-emerald-400 opacity-60 motion-reduce:hidden" />
        )}
        <span
          className={`relative inline-flex h-2 w-2 rounded-full ${
            online ? "bg-emerald-500" : "bg-rose-500"
          }`}
        />
      </span>
      <span
        className={`hidden lg:inline ${online ? "text-slate-600" : "text-rose-600"}`}
      >
        {label}
      </span>
      <span className="sr-only lg:hidden">
        {label}. {detail}
      </span>
    </div>
  );
}

function UserMenu() {
  const { role, persona, setRole } = useRole();
  const [open, setOpen] = useState(false);
  const wrapperRef = useRef<HTMLDivElement>(null);
  const buttonRef = useRef<HTMLButtonElement>(null);
  const itemRefs = useRef<(HTMLButtonElement | null)[]>([]);

  useEffect(() => {
    if (!open) return;
    itemRefs.current[ROLE_ORDER.indexOf(role)]?.focus();
    const onPointerDown = (e: MouseEvent) => {
      if (!wrapperRef.current?.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", onPointerDown);
    return () => document.removeEventListener("mousedown", onPointerDown);
  }, [open, role]);

  const close = (refocus = true) => {
    setOpen(false);
    if (refocus) buttonRef.current?.focus();
  };

  const onMenuKeyDown = (e: React.KeyboardEvent) => {
    const items = itemRefs.current.filter(Boolean) as HTMLButtonElement[];
    const idx = items.indexOf(document.activeElement as HTMLButtonElement);
    if (e.key === "Escape") {
      e.preventDefault();
      close();
    } else if (e.key === "Tab") {
      close(false);
    } else if (e.key === "ArrowDown" || e.key === "ArrowUp") {
      e.preventDefault();
      const step = e.key === "ArrowDown" ? 1 : -1;
      items[(idx + step + items.length) % items.length]?.focus();
    } else if (e.key === "Home" || e.key === "End") {
      e.preventDefault();
      items[e.key === "Home" ? 0 : items.length - 1]?.focus();
    }
  };

  return (
    <div ref={wrapperRef} className="relative">
      <button
        ref={buttonRef}
        type="button"
        aria-haspopup="menu"
        aria-expanded={open}
        aria-label={`Signed in as ${persona.fullName}, ${persona.label}. Switch demo role`}
        onClick={() => setOpen((o) => !o)}
        className="flex min-h-[44px] items-center gap-2.5 rounded-lg px-2 py-1 transition-colors hover:bg-slate-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-500"
      >
        <span
          className={`flex h-8 w-8 items-center justify-center rounded-full text-xs font-semibold text-white ${ROLE_AVATAR[role]}`}
          aria-hidden="true"
        >
          {initials(persona.fullName)}
        </span>
        <span className="hidden text-left leading-tight sm:block" aria-hidden="true">
          <span className="block text-sm font-semibold text-slate-900">
            {persona.fullName}
          </span>
          <span className="block text-xs text-slate-500">{persona.label}</span>
        </span>
        <ChevronDown
          className={`h-4 w-4 text-slate-400 transition-transform duration-150 ${
            open ? "rotate-180" : ""
          }`}
          aria-hidden="true"
        />
      </button>

      {open && (
        <div
          role="menu"
          aria-label="Switch demo role"
          onKeyDown={onMenuKeyDown}
          className="absolute right-0 top-full z-40 mt-2 w-72 overflow-hidden rounded-xl border border-slate-200 bg-white shadow-lg"
        >
          <div className="border-b border-slate-100 px-4 py-3">
            <p className="text-sm font-semibold text-slate-900">{persona.fullName}</p>
            <p className="truncate text-xs text-slate-500">{persona.email}</p>
          </div>
          <p className="px-4 pb-1 pt-3 text-xs font-medium text-slate-500">
            Switch demo role
          </p>
          <div className="pb-2">
            {ROLE_ORDER.map((r, i) => {
              const p = PERSONAS[r];
              const selected = r === role;
              return (
                <button
                  key={r}
                  ref={(el) => {
                    itemRefs.current[i] = el;
                  }}
                  type="button"
                  role="menuitemradio"
                  aria-checked={selected}
                  onClick={() => {
                    setRole(r);
                    close();
                  }}
                  className="flex w-full items-center gap-3 px-4 py-2 text-left transition-colors hover:bg-slate-50 focus-visible:bg-slate-100 focus-visible:outline-none"
                >
                  <span
                    className={`flex h-7 w-7 shrink-0 items-center justify-center rounded-full text-[11px] font-semibold text-white ${ROLE_AVATAR[r]}`}
                    aria-hidden="true"
                  >
                    {initials(p.fullName)}
                  </span>
                  <span className="min-w-0 flex-1 leading-tight">
                    <span className="block text-sm font-medium text-slate-900">
                      {p.label}
                    </span>
                    <span className="block truncate text-xs text-slate-500">
                      {p.fullName} · {p.department}
                    </span>
                  </span>
                  {selected && (
                    <Check className="h-4 w-4 shrink-0 text-blue-600" aria-hidden="true" />
                  )}
                </button>
              );
            })}
          </div>
        </div>
      )}
    </div>
  );
}

export function TopNavBar() {
  const pathname = usePathname();
  const { health } = useRole();

  return (
    <header className="sticky top-0 z-30 border-b border-slate-200 bg-white/90 backdrop-blur">
      <div className="mx-auto flex max-w-7xl flex-wrap items-center gap-x-8 px-4 sm:px-6 lg:px-8">
        <Link
          href="/"
          className="flex h-16 items-center gap-2.5 rounded-md focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-500"
        >
          <span className="flex h-8 w-8 items-center justify-center rounded-lg bg-blue-600 text-white">
            <ShieldCheck className="h-[18px] w-[18px]" aria-hidden="true" />
          </span>
          <span className="text-base font-bold tracking-tight text-slate-900">
            AI-HAS
          </span>
        </Link>

        <nav
          aria-label="Portals"
          className="order-last -mx-4 flex w-[calc(100%+2rem)] border-t border-slate-100 px-4 md:order-none md:mx-0 md:w-auto md:border-t-0 md:px-0"
        >
          {NAV_ITEMS.map((item) => {
            const Icon = item.icon;
            const active = pathname === item.href;
            return (
              <Link
                key={item.href}
                href={item.href}
                aria-current={active ? "page" : undefined}
                className={`-mb-px flex h-12 flex-1 items-center justify-center gap-1.5 whitespace-nowrap border-b-2 px-1 text-sm md:gap-2 md:px-3 font-medium transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-blue-500 md:h-16 md:flex-none ${
                  active
                    ? "border-blue-600 text-blue-700"
                    : "border-transparent text-slate-600 hover:border-slate-300 hover:text-slate-900"
                }`}
              >
                <Icon className="h-4 w-4" aria-hidden="true" />
                {item.label}
              </Link>
            );
          })}
        </nav>

        <div className="ml-auto flex h-16 items-center gap-4">
          <StatusIndicator health={health} />
          <span className="h-6 w-px bg-slate-200" aria-hidden="true" />
          <UserMenu />
        </div>
      </div>
    </header>
  );
}
