"use client";

import { usePathname } from "next/navigation";
import Sidebar from "@/components/Sidebar";
import TopBar from "@/components/TopBar";

// Pages that render full-screen without the sidebar and top bar.
const BARE_PATHS = ["/login", "/onboarding"];

export default function AppShell({ children }) {
  const pathname = usePathname();
  if (BARE_PATHS.includes(pathname)) return <>{children}</>;
  return (
    <div className="app-container">
      <Sidebar />
      <div className="main-content">
        <TopBar />
        <div className="page">{children}</div>
      </div>
    </div>
  );
}
