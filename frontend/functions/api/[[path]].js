// Cloudflare Pages Function — reverse-proxies every /api/* request to the Railway backend.
//
// Why: the SPA calls the API at the same origin ("/api/...", see src/lib/api.ts BASE="/api"),
// which keeps the Authorization header and avoids CORS entirely. In dev, vite.config does this;
// in production, this Function is the equivalent. It strips the "/api" prefix and forwards the
// path + query + method + body + headers to the backend, mirroring the dev proxy's rewrite.
//
// Setup: in the Cloudflare Pages project, add an environment variable
//   API_BASE_URL = https://<your-railway-api-domain>     (no trailing slash needed)

export async function onRequest(context) {
  const { request, env } = context;
  const url = new URL(request.url);

  const base = (env.API_BASE_URL || "").replace(/\/$/, "");
  if (!base) {
    return new Response("API_BASE_URL is not configured for this Pages project", { status: 500 });
  }

  // "/api/auth/login" -> "/auth/login" (same rewrite as the Vite dev proxy).
  const path = url.pathname.replace(/^\/api/, "") || "/";
  const target = base + path + url.search;

  const headers = new Headers(request.headers);
  headers.delete("host"); // let fetch set the correct Host for the backend

  const init = {
    method: request.method,
    headers,
    redirect: "manual",
  };
  if (request.method !== "GET" && request.method !== "HEAD") {
    init.body = request.body;
    init.duplex = "half"; // required to stream a request body (e.g. recording uploads)
  }

  return fetch(target, init);
}
