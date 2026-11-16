import type { Metadata } from "next";
import "./globals.css";
import { RoleProvider, TopNavBar } from "@/components/RoleSwitcher";

export const metadata: Metadata = {
  title: "AI-HAS | Enterprise Helpdesk & Workflow Automation",
  description:
    "AI-Powered Enterprise Helpdesk & Workflow Automation System with Hybrid RAG, Guardrails, Tool Execution, and Human-in-the-Loop Support.",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en">
      <body className="min-h-screen bg-slate-50 text-slate-900 antialiased">
        <RoleProvider>
          <div className="flex min-h-screen flex-col">
            <TopNavBar />
            <main className="mx-auto w-full max-w-7xl flex-1 px-4 py-6 sm:px-6 lg:px-8">
              {children}
            </main>
          </div>
        </RoleProvider>
      </body>
    </html>
  );
}
