import type { Page, Request } from "@playwright/test";
import { defaultFixtures, type Fixtures } from "./fixtures";

export const API_BASE = (process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8021").replace(/\/$/, "");

type RouteHandler = (request: Request, fixtures: Fixtures) => unknown;

type RouteDef = {
  pattern: string;
  handler: RouteHandler;
};

function matchRoute(method: string, path: string, pattern: string): boolean {
  const [patternMethod, patternPath] = pattern.split(" ");
  if (method !== patternMethod) return false;
  const patternSegments = patternPath.split("/").filter(Boolean);
  const segments = path.split("/").filter(Boolean);
  if (segments.length < patternSegments.length) return false;
  for (let i = 0; i < patternSegments.length; i++) {
    if (patternSegments[i] === "*") return true;
    if (patternSegments[i] !== segments[i]) return false;
  }
  return segments.length === patternSegments.length;
}

function parsePath(url: string): { pathname: string; search: string } {
  const parsed = new URL(url);
  return { pathname: parsed.pathname, search: parsed.search };
}

const routeDefinitions: RouteDef[] = [
  { pattern: "GET /api/v1/auth/me", handler: (_request, fixtures) => ({ user: fixtures.user }) },
  { pattern: "POST /api/v1/auth/refresh", handler: (_request, fixtures) => ({ user: fixtures.user }) },
  { pattern: "POST /api/v1/auth/login", handler: (_request, fixtures) => ({ user: fixtures.user }) },
  { pattern: "POST /api/v1/auth/logout", handler: () => null },
  { pattern: "POST /api/v1/auth/register", handler: (request, fixtures) => request.postDataJSON() ?? { user: fixtures.user } },
  { pattern: "GET /api/v1/preferences", handler: (_request, fixtures) => fixtures.preferences },
  { pattern: "GET /api/v1/admin/overview", handler: (_request, fixtures) => fixtures.adminOverview },
  { pattern: "GET /api/v1/admin/jobs", handler: (_request, fixtures) => fixtures.adminJobs },
  { pattern: "GET /api/v1/admin/observability", handler: (_request, fixtures) => fixtures.adminObservability },
  { pattern: "GET /api/v1/admin/scraper/config", handler: (_request, fixtures) => fixtures.scraperConfig },
  { pattern: "PUT /api/v1/admin/scraper/config", handler: (request) => request.postDataJSON() },
  { pattern: "POST /api/v1/admin/scraper/runs", handler: (_request, fixtures) => fixtures.queuedScrapeRun },
  { pattern: "GET /api/v1/admin/scraper/runs/latest", handler: (_request, fixtures) => fixtures.latestScraperRun },
  { pattern: "GET /api/v1/admin/pipeline/stats", handler: (_request, fixtures) => fixtures.pipelineStats },
  { pattern: "GET /api/v1/admin/pipeline/runs/latest", handler: (_request, fixtures) => fixtures.latestPipelineRun },
  { pattern: "POST /api/v1/admin/pipeline/*", handler: (_request, fixtures) => fixtures.queuedPipelineRun },
  { pattern: "GET /api/v1/admin/trend-content", handler: (_request, fixtures) => fixtures.trendContentList },
  { pattern: "GET /api/v1/admin/trend-content/*", handler: (_request, fixtures) => fixtures.trendContentDetail },
  { pattern: "GET /api/v1/admin/profiling/config", handler: (_request, fixtures) => fixtures.profilingConfig },
  { pattern: "PUT /api/v1/admin/profiling/config", handler: (request) => request.postDataJSON() },
  { pattern: "GET /api/v1/admin/profiling/estimate", handler: (_request, fixtures) => fixtures.profilingEstimate },
  { pattern: "POST /api/v1/admin/profiling/runs", handler: (_request, fixtures) => fixtures.queuedProfilingRun },
  { pattern: "GET /api/v1/admin/profiling/runs/latest", handler: (_request, fixtures) => fixtures.latestProfilingRun },
  { pattern: "GET /api/v1/instagram/status", handler: (_request, fixtures) => fixtures.instagramStatus },
  { pattern: "GET /api/v1/profile/analytics", handler: (_request, fixtures) => fixtures.profileAnalytics },
  { pattern: "GET /api/v1/recommendations", handler: (_request, fixtures) => fixtures.recommendations },
];

export async function mockApi(
  page: Page,
  overrides: Partial<Fixtures> = {},
  options: { authenticated?: boolean } = {},
) {
  const fixtures: Fixtures = { ...defaultFixtures, ...overrides };
  let isAuthenticated = options.authenticated ?? true;

  await page.unrouteAll({ behavior: "ignoreErrors" }).catch(() => undefined);

  // Scraper/Profiling pages open WebSocket log streams. Real WebSockets cannot
  // connect in tests and cause delayed re-renders / console errors. Replace the
  // constructor with a fake that immediately closes so the UI stays stable.
  await page.addInitScript(() => {
    class FakeWebSocket {
      onopen: ((ev: Event) => void) | null = null;
      onmessage: ((ev: MessageEvent) => void) | null = null;
      onerror: ((ev: Event) => void) | null = null;
      onclose: ((ev: CloseEvent) => void) | null = null;
      constructor() {
        setTimeout(() => {
          if (this.onclose) {
            this.onclose(new CloseEvent("close", { wasClean: true, code: 1000 }));
          }
        }, 0);
      }
      send() {}
      close() {}
    }
    Object.assign(window, { WebSocket: FakeWebSocket });
  });

  await page.route(`${API_BASE}/**/*`, async (route, request) => {
    const origin = request.headers()["origin"] ?? "http://localhost:8020";
    const corsHeaders = {
      "Access-Control-Allow-Origin": origin,
      "Access-Control-Allow-Credentials": "true",
      "Access-Control-Allow-Methods": "GET, POST, PUT, DELETE, PATCH, OPTIONS",
      "Access-Control-Allow-Headers": "Content-Type, Authorization, Bypass-Tunnel-Reminder",
    };

    const { pathname } = parsePath(request.url());

    if (request.method() === "OPTIONS") {
      await route.fulfill({ status: 204, headers: corsHeaders });
      return;
    }

    // The setup test needs to start unauthenticated and log in through the UI.
    if (request.method() === "POST" && pathname === "/api/v1/auth/refresh") {
      if (!isAuthenticated) {
        await route.fulfill({ status: 401, headers: corsHeaders });
      } else {
        await route.fulfill({
          status: 200,
          headers: { ...corsHeaders, "Content-Type": "application/json" },
          body: JSON.stringify({ user: fixtures.user }),
        });
      }
      return;
    }

    for (const def of routeDefinitions) {
      const [method, ...pathParts] = def.pattern.split(" ");
      const pathPattern = pathParts.join(" ");
      if (matchRoute(request.method(), pathname, `${method} ${pathPattern}`)) {
        const responseBody = def.handler(request, fixtures);

        if (request.method() === "POST" && pathname === "/api/v1/auth/logout") {
          isAuthenticated = false;
        } else if (
          request.method() === "POST" &&
          (pathname === "/api/v1/auth/login" || pathname === "/api/v1/auth/register")
        ) {
          isAuthenticated = true;
        }

        const body = responseBody === undefined ? undefined : JSON.stringify(responseBody);
        await route.fulfill({
          status: 200,
          headers: { ...corsHeaders, "Content-Type": "application/json" },
          body,
        });
        return;
      }
    }

    await route.fulfill({
      status: 404,
      headers: { ...corsHeaders, "Content-Type": "application/json" },
      body: JSON.stringify({ detail: `E2E route not mocked: ${request.method()} ${pathname}` }),
    });
  });
}
