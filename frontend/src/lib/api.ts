import type {
  AdminObservability,
  AdminOverview,
  BrandAnalysisPost,
  BrandAnalysisReport,
  BrandAnalysisRequest,
  CreatorContentListResponse,
  CreatorPreferences,
  CreatorPreferencesUpdate,
  EvaluationRun,
  EvaluationRunRequest,
  FollowerHistory,
  FollowerHistoryRange,
  ProfileAnalytics,
  TrackedCreator,
  TrackedCreatorDetail,
  AuthResponse,
  InstagramStatus,
  Job,
  JobKind,
  PipelineStats,
  ProfilingConfig,
  ProfilingEstimate,
  RecommendationBatch,
  RecommendationEvent,
  RecommendationExperiment,
  RecommendationExperimentCreate,
  RecommendationPostLink,
  RecommendationState,
  ScraperConfig,
  TrendContent,
  TrendContentFilters,
  TrendContentListResponse,
} from "@/lib/types";
import {
  normalizeArray,
  normalizeInstagramStatus,
  normalizeJob,
  normalizeRecommendationBatch,
  normalizeUser,
} from "@/lib/validators";

export const API_BASE_URL = (
  process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8021"
).replace(/\/$/, "");

const TUNNEL_REMINDER_HEADER = API_BASE_URL.includes(".loca.lt")
  ? { "Bypass-Tunnel-Reminder": "true" }
  : undefined;

export function wsUrl(path: string): string {
  const base = API_BASE_URL.replace(/^http/, "ws");
  return `${base}${path}`;
}

const DEFAULT_TIMEOUT_MS = 30_000;

export type ApiErrorKind = "timeout" | "network" | "http" | "parse";

export class ApiError extends Error {
  constructor(
    message: string,
    public readonly status: number = 0,
    public readonly details?: unknown,
    public readonly kind: ApiErrorKind = "http",
  ) {
    super(message);
    this.name = "ApiError";
  }
}

function isAbortError(caught: unknown): caught is Error | DOMException {
  const hasAbortName =
    (caught instanceof Error || caught instanceof DOMException) &&
    (caught.name === "AbortError" || caught.name === "TimeoutError");

  return hasAbortName;
}

async function parseResponse(response: Response): Promise<unknown> {
  if (response.status === 204) return undefined;

  try {
    const text = await response.text();
    if (!text) return undefined;

    try {
      return JSON.parse(text);
    } catch {
      return text;
    }
  } catch {
    return undefined;
  }
}

function errorMessage(payload: unknown, fallback: string): string {
  if (typeof payload === "string" && payload.trim()) return payload;
  if (payload && typeof payload === "object") {
    const record = payload as Record<string, unknown>;
    const candidate = record.detail ?? record.message ?? record.error;
    if (typeof candidate === "string") return candidate;
    if (Array.isArray(candidate)) {
      return candidate
        .map((item) =>
          typeof item === "object" && item && "msg" in item
            ? String((item as { msg: unknown }).msg)
            : String(item),
        )
        .join(", ");
    }
  }
  return fallback;
}

function buildHeaders(init: RequestInit): Headers {
  const headers = new Headers(init.headers);
  if (init.body && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }

  if (API_BASE_URL.includes(".loca.lt")) {
    headers.set("Bypass-Tunnel-Reminder", "true");
  }

  return headers;
}

async function fetchWithTimeout(
  input: RequestInfo | URL,
  init?: RequestInit,
): Promise<Response> {
  try {
    return await fetch(input, {
      ...init,
      signal: AbortSignal.timeout(DEFAULT_TIMEOUT_MS),
    });
  } catch (caught) {
    if (isAbortError(caught)) {
      throw new ApiError(
        "Request timed out. Please try again.",
        0,
        undefined,
        "timeout",
      );
    }
    throw new ApiError(
      "Network error. Check your connection and try again.",
      0,
      undefined,
      "network",
    );
  }
}

async function refreshSession(): Promise<boolean> {
  try {
    const refreshResponse = await fetchWithTimeout(
      `${API_BASE_URL}/api/v1/auth/refresh`,
      {
        method: "POST",
        credentials: "include",
        headers: TUNNEL_REMINDER_HEADER,
      },
    );
    return refreshResponse.ok;
  } catch {
    return false;
  }
}

async function request<T>(
  path: string,
  init: RequestInit = {},
  canRefresh = true,
): Promise<T> {
  const response = await fetchWithTimeout(`${API_BASE_URL}${path}`, {
    ...init,
    headers: buildHeaders(init),
    credentials: "include",
  });

  if (response.status === 401 && canRefresh) {
    const refreshed = await refreshSession();
    if (refreshed) return request<T>(path, init, false);
  }

  const payload = await parseResponse(response);
  if (!response.ok) {
    throw new ApiError(
      errorMessage(payload, `Request failed (${response.status})`),
      response.status,
      payload,
      "http",
    );
  }
  return payload as T;
}

export const api = {
  register: async (email: string, password: string) =>
    (await request<AuthResponse>("/api/v1/auth/register", {
      method: "POST",
      body: JSON.stringify({ email, password }),
    })).user,
  login: async (email: string, password: string) =>
    (await request<AuthResponse>("/api/v1/auth/login", {
      method: "POST",
      body: JSON.stringify({ email, password }),
    })).user,
  refresh: async () => {
    try {
      return normalizeUser(
        (await request<AuthResponse>("/api/v1/auth/refresh", { method: "POST" }, false)).user,
      );
    } catch (error) {
      if (error instanceof ApiError && error.status === 401) return null;
      throw error;
    }
  },
  logout: () => request<void>("/api/v1/auth/logout", { method: "POST" }),
  me: async () => normalizeUser((await request<AuthResponse>("/api/v1/auth/me")).user),
  getPreferences: () =>
    request<CreatorPreferences>("/api/v1/preferences"),
  updatePreferences: (preferences: CreatorPreferencesUpdate) =>
    request<CreatorPreferences>("/api/v1/preferences", {
      method: "PUT",
      body: JSON.stringify(preferences),
    }),
  getInstagramStatus: () =>
    request<InstagramStatus>("/api/v1/instagram/status").then(
      normalizeInstagramStatus,
    ),
  startInstagramOAuth: () =>
    request<{ authorization_url: string }>("/api/v1/instagram/oauth/start", {
      method: "POST",
    }),
  disconnectInstagram: () =>
    request<void>("/api/v1/instagram/connection", { method: "DELETE" }),
  syncProfile: () =>
    request<Job>("/api/v1/profile/sync", { method: "POST" }).then(normalizeJob),
  getProfileAnalytics: () =>
    request<ProfileAnalytics>("/api/v1/profile/analytics"),
  getRecommendations: (limit = 10) =>
    request<RecommendationBatch[]>(
      `/api/v1/recommendations?limit=${encodeURIComponent(limit)}`,
    ).then((items) => normalizeArray(items, normalizeRecommendationBatch)),
  createRecommendations: (count = 3) =>
    request<RecommendationBatch>("/api/v1/recommendations", {
      method: "POST",
      body: JSON.stringify({ count }),
    }).then(normalizeRecommendationBatch),
  createRecommendationEvent: (
    recommendationId: string,
    event: {
      state: RecommendationState;
      reason?: string;
      note?: string;
      idempotency_key: string;
    },
  ) =>
    request<RecommendationEvent>(
      `/api/v1/recommendations/${encodeURIComponent(recommendationId)}/events`,
      { method: "POST", body: JSON.stringify(event) },
    ),
  linkRecommendationPost: (
    recommendationId: string,
    mediaId: string,
  ) =>
    request<RecommendationPostLink>(
      `/api/v1/recommendations/${encodeURIComponent(recommendationId)}/post-link`,
      { method: "POST", body: JSON.stringify({ media_id: mediaId }) },
    ),
  createRecommendationExperiment: (experiment: RecommendationExperimentCreate) =>
    request<RecommendationExperiment>("/api/v1/recommendation-experiments", {
      method: "POST",
      body: JSON.stringify(experiment),
    }),
  updateRecommendationExperiment: (
    experimentId: string,
    state: RecommendationExperiment["state"],
    note?: string,
  ) =>
    request<RecommendationExperiment>(
      `/api/v1/recommendation-experiments/${encodeURIComponent(experimentId)}`,
      {
        method: "PATCH",
        body: JSON.stringify({ state, ...(note ? { note } : {}) }),
      },
    ),
  getScraperConfig: () =>
    request<ScraperConfig>("/api/v1/admin/scraper/config"),
  updateScraperConfig: (config: ScraperConfig) =>
    request<ScraperConfig>("/api/v1/admin/scraper/config", {
      method: "PUT",
      body: JSON.stringify(config),
    }),
  startScraperRun: () =>
    request<Job>("/api/v1/admin/scraper/runs", { method: "POST", body: "{}" }).then(
      normalizeJob,
    ),
  getLatestScraperRun: () =>
    request<Job>("/api/v1/admin/scraper/runs/latest").then(normalizeJob),
  getScraperRun: (id: string | number) =>
    request<Job>(`/api/v1/admin/scraper/runs/${id}`).then(normalizeJob),
  startPipeline: (stage: Exclude<JobKind, "scrape" | "pipeline">) =>
    request<Job>(`/api/v1/admin/pipeline/${stage}`, { method: "POST" }).then(
      normalizeJob,
    ),
  startFullPipeline: () =>
    request<Job>("/api/v1/admin/pipeline/run", { method: "POST" }).then(
      normalizeJob,
    ),
  getLatestPipelineRun: (kind?: Exclude<JobKind, "scrape">) =>
    request<Job>(
      `/api/v1/admin/pipeline/runs/latest${kind ? `?kind=${kind}` : ""}`,
    ).then(normalizeJob),
  getPipelineRun: (id: string | number) =>
    request<Job>(`/api/v1/admin/pipeline/runs/${id}`).then(normalizeJob),
  stopJob: (id: string) =>
    request<Job>(`/api/v1/admin/jobs/${encodeURIComponent(id)}/stop`, {
      method: "POST",
    }).then(normalizeJob),
  submitInterventionResponse: (
    id: string,
    body: { code: string; action?: string | null },
  ) =>
    request<Job>(`/api/v1/admin/jobs/${encodeURIComponent(id)}/intervention`, {
      method: "POST",
      body: JSON.stringify(body),
    }).then(normalizeJob),
  getPipelineStats: () =>
    request<PipelineStats>("/api/v1/admin/pipeline/stats"),
  getTrendContent: (params: TrendContentFilters = {}) => {
    const search = new URLSearchParams();
    if (params.status != null) search.set("status", params.status);
    if (params.job_id != null) search.set("job_id", params.job_id);
    if (params.action != null) search.set("action", params.action);
    if (params.keyword != null) search.set("keyword", params.keyword);
    if (params.search != null) search.set("search", params.search);
    if (params.sort != null) search.set("sort", params.sort);
    if (params.limit != null) search.set("limit", String(params.limit));
    if (params.offset != null) search.set("offset", String(params.offset));
    const query = search.toString();
    return request<TrendContentListResponse>(
      `/api/v1/admin/trend-content${query ? `?${query}` : ""}`,
    );
  },
  getTrendContentDetail: (id: string) =>
    request<TrendContent>(`/api/v1/admin/trend-content/${id}`),
  getAdminOverview: () => request<AdminOverview>("/api/v1/admin/overview"),
  getAdminObservability: () =>
    request<AdminObservability>("/api/v1/admin/observability"),
  runOfflineEvaluation: (evaluation: EvaluationRunRequest) =>
    request<EvaluationRun>("/api/v1/admin/evaluations/run", {
      method: "POST",
      body: JSON.stringify(evaluation),
    }),
  getAdminJobs: (params: { limit?: number; state?: string; kind?: string } = {}) => {
    const search = new URLSearchParams();
    if (params.limit != null) search.set("limit", String(params.limit));
    if (params.state) search.set("state", params.state);
    if (params.kind) search.set("kind", params.kind);
    const query = search.toString();
    return request<Job[]>(`/api/v1/admin/jobs${query ? `?${query}` : ""}`).then(
      (items) => normalizeArray(items, normalizeJob),
    );
  },
  getProfilingConfig: () =>
    request<ProfilingConfig>("/api/v1/admin/profiling/config"),
  updateProfilingConfig: (config: ProfilingConfig) =>
    request<ProfilingConfig>("/api/v1/admin/profiling/config", {
      method: "PUT",
      body: JSON.stringify(config),
    }),
  getProfilingEstimate: () =>
    request<ProfilingEstimate>("/api/v1/admin/profiling/estimate"),
  startProfilingRun: () =>
    request<Job>("/api/v1/admin/profiling/runs", {
      method: "POST",
      body: "{}",
    }).then(normalizeJob),
  getLatestProfilingRun: () =>
    request<Job>("/api/v1/admin/profiling/runs/latest").then(normalizeJob),
  startBrandAnalysis: (req: BrandAnalysisRequest) =>
    request<Job>("/api/v1/admin/brand-analysis/runs", {
      method: "POST",
      body: JSON.stringify(req),
    }).then(normalizeJob),
  getBrandAnalysisJob: (id: string) =>
    request<Job>(`/api/v1/admin/brand-analysis/runs/${encodeURIComponent(id)}`).then(
      normalizeJob,
    ),
  getBrandAnalysisReport: (id: string) =>
    request<BrandAnalysisReport>(
      `/api/v1/admin/brand-analysis/reports/${encodeURIComponent(id)}`,
    ),
  exportBrandAnalysisPdf: async (id: string): Promise<Blob> => {
    const path = `/api/v1/admin/brand-analysis/reports/${encodeURIComponent(id)}/pdf`;
    const headers = new Headers({ Accept: "application/pdf", ...TUNNEL_REMINDER_HEADER });
    let response = await fetchWithTimeout(`${API_BASE_URL}${path}`, {
      headers,
      credentials: "include",
    });
    if (response.status === 401) {
      const refreshed = await refreshSession();
      if (refreshed) {
        response = await fetchWithTimeout(`${API_BASE_URL}${path}`, {
          headers,
          credentials: "include",
        });
      }
    }
    if (!response.ok) {
      const payload = await parseResponse(response);
      throw new ApiError(
        errorMessage(payload, `Request failed (${response.status})`),
        response.status,
        payload,
        "http",
      );
    }
    return response.blob();
  },
  getBrandAnalysisPosts: (jobId: string, limit = 30, offset = 0) =>
    request<BrandAnalysisPost[]>(
      `/api/v1/admin/brand-analysis/runs/${encodeURIComponent(jobId)}/posts?` +
        new URLSearchParams({ limit: String(limit), offset: String(offset) }),
    ),
  addTrackedCreator: (username: string) =>
    request<TrackedCreator>("/api/v1/creators", {
      method: "POST",
      body: JSON.stringify({ username }),
    }),
  getTrackedCreators: async () =>
    (await request<{ creators: TrackedCreator[] }>("/api/v1/creators")).creators,
  getTrackedCreator: (creatorId: string) =>
    request<TrackedCreatorDetail>(
      `/api/v1/creators/${encodeURIComponent(creatorId)}`,
    ),
  getTrackedCreatorFollowers: (creatorId: string, range: FollowerHistoryRange) =>
    request<FollowerHistory>(
      `/api/v1/creators/${encodeURIComponent(creatorId)}/followers?` +
        new URLSearchParams({ range }),
    ),
  getTrackedCreatorContent: (creatorId: string, sort: "recent" | "viral" = "recent") =>
    request<CreatorContentListResponse>(
      `/api/v1/creators/${encodeURIComponent(creatorId)}/content?` +
        new URLSearchParams({ sort }),
    ),
  analyzeTrackedCreator: (creatorId: string) =>
    request<Job>(`/api/v1/creators/${encodeURIComponent(creatorId)}/analyze`, {
      method: "POST",
    }).then(normalizeJob),
  removeTrackedCreator: (creatorId: string) =>
    request<void>(`/api/v1/creators/${encodeURIComponent(creatorId)}`, {
      method: "DELETE",
    }),
};
