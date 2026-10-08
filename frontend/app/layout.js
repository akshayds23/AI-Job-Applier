import "./globals.css";
import AppShell from "@/components/AppShell";
import AuthGuard from "@/components/AuthGuard";

export const metadata = {
  title: { default: "AutoApplier", template: "%s | AutoApplier" },
  applicationName: "AutoApplier",
  description: "Find verified jobs, tailor your resume truthfully, apply on company sites and track replies.",
};

export const viewport = { themeColor: "#2563eb" };

// Applied before first paint so the saved theme never flashes the wrong colours.
const themeScript = `
try {
  var t = localStorage.getItem("theme");
  if (t === "dark" || t === "light") document.documentElement.setAttribute("data-theme", t);
} catch (e) {}
`;

export default function RootLayout({ children }) {
  return (
    <html lang="en" data-theme="light" suppressHydrationWarning>
      <head>
        <script dangerouslySetInnerHTML={{ __html: themeScript }} />
      </head>
      <body>
        <AuthGuard>
          <AppShell>{children}</AppShell>
        </AuthGuard>
      </body>
    </html>
  );
}
