"use client";

import { useEffect, useState } from "react";
import { usePathname, useRouter } from "next/navigation";
import { Loader2 } from "lucide-react";

const PUBLIC_PATHS = ["/login"];

export default function AuthGuard({ children }) {
  const pathname = usePathname();
  const router = useRouter();
  const [authorized, setAuthorized] = useState(false);

  useEffect(() => {
    if (PUBLIC_PATHS.includes(pathname)) {
      setAuthorized(true);
      return;
    }
    const token = typeof window !== "undefined" ? sessionStorage.getItem("app_auth_token") : null;
    if (!token) {
      setAuthorized(false);
      router.replace("/login");
    } else {
      setAuthorized(true);
    }
  }, [pathname, router]);

  if (PUBLIC_PATHS.includes(pathname)) return <>{children}</>;

  if (!authorized) {
    return (
      <div style={{ height: "100vh", display: "grid", placeItems: "center", color: "var(--text-2)" }}>
        <div className="row"><Loader2 size={18} className="spin" /> Checking your session...</div>
      </div>
    );
  }

  return <>{children}</>;
}
