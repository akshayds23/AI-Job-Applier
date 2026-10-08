// Web app manifest: name and icons when AutoApplier is installed or pinned to a home screen.
export default function manifest() {
  return {
    name: "AutoApplier",
    short_name: "AutoApplier",
    description: "Find verified jobs, tailor your resume truthfully, apply on company sites and track replies.",
    start_url: "/",
    display: "standalone",
    background_color: "#f5f7fa",
    theme_color: "#2563eb",
    icons: [
      { src: "/icon-192.png", sizes: "192x192", type: "image/png" },
      { src: "/icon-512.png", sizes: "512x512", type: "image/png" },
      { src: "/icon-512.png", sizes: "512x512", type: "image/png", purpose: "maskable" },
    ],
  };
}
