import { ApiError, api } from "@/lib/api";

describe("api client", () => {
  it("sends JSON and cookie credentials", async () => {
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValue(
        new Response(
          JSON.stringify({
            user: { email: "user@example.com", role: "user" },
          }),
          { status: 200 },
        ),
      );

    await expect(api.login("user@example.com", "password123")).resolves.toMatchObject({
      role: "user",
    });

    expect(fetchMock).toHaveBeenCalledWith(
      "http://localhost:8021/api/v1/auth/login",
      expect.objectContaining({
        method: "POST",
        credentials: "include",
        body: JSON.stringify({
          email: "user@example.com",
          password: "password123",
        }),
      }),
    );
    const headers = fetchMock.mock.calls[0][1]?.headers as Headers;
    expect(headers.get("Content-Type")).toBe("application/json");
  });

  it("refreshes once after a 401 and retries the original request", async () => {
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValueOnce(new Response(null, { status: 401 }))
      .mockResolvedValueOnce(new Response(null, { status: 204 }))
      .mockResolvedValueOnce(
        new Response(
          JSON.stringify({
            user: { email: "admin@example.com", role: "admin" },
          }),
          { status: 200 },
        ),
      );

    await expect(api.me()).resolves.toMatchObject({ role: "admin" });
    expect(fetchMock).toHaveBeenCalledTimes(3);
    expect(fetchMock.mock.calls[1][0]).toBe(
      "http://localhost:8021/api/v1/auth/refresh",
    );
    expect(fetchMock.mock.calls[2][0]).toBe(
      "http://localhost:8021/api/v1/auth/me",
    );
  });

  it("surfaces the backend error detail", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValueOnce(
      new Response(JSON.stringify({ detail: "Invalid credentials" }), {
        status: 400,
      }),
    );

    await expect(api.login("user@example.com", "password123")).rejects.toEqual(
      expect.objectContaining<ApiError>({
        status: 400,
        message: "Invalid credentials",
        name: "ApiError",
        kind: "http",
      }),
    );
  });

  it("uses the Instagram and profiling endpoint contracts", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockImplementation(async (url) => {
      const path = String(url);
      if (path.endsWith("/instagram/status")) {
        return new Response(JSON.stringify({ status: "ready" }));
      }
      if (path.endsWith("/instagram/oauth/start")) {
        return new Response(JSON.stringify({ authorization_url: "https://instagram.test" }));
      }
      if (path.endsWith("/instagram/connection")) {
        return new Response(null, { status: 204 });
      }
      if (path.endsWith("/admin/profiling/config")) {
        return new Response(JSON.stringify({ enabled: true, schedule_cron: "0 3 * * *" }));
      }
      if (path.endsWith("/admin/profiling/estimate")) {
        return new Response(
          JSON.stringify({
            connected_users: 2,
            average_seconds_per_user: 4,
            estimated_duration_seconds: 8,
            estimated_start_at: null,
            estimated_finish_at: null,
          }),
        );
      }
      return new Response(
        JSON.stringify({ id: "job-1", kind: "profile", state: "queued", counters: {} }),
      );
    });

    await Promise.all([
      api.getInstagramStatus(),
      api.startInstagramOAuth(),
      api.disconnectInstagram(),
      api.syncProfile(),
      api.getProfilingConfig(),
      api.updateProfilingConfig({ enabled: true, schedule_cron: "0 3 * * *" }),
      api.getProfilingEstimate(),
      api.startProfilingRun(),
      api.getLatestProfilingRun(),
    ]);

    const calls = fetchMock.mock.calls.map(([url, init]) => ({
      url: String(url),
      method: init?.method ?? "GET",
    }));
    expect(
      calls.some(
        ({ url, method }) =>
          url.endsWith("/api/v1/profile/sync") && method === "POST",
      ),
    ).toBe(true);
    expect(
      calls.some(
        ({ url, method }) =>
          url.endsWith("/api/v1/instagram/connection") && method === "DELETE",
      ),
    ).toBe(true);
    expect(
      calls.some(
        ({ url, method }) =>
          url.endsWith("/api/v1/admin/profiling/runs") && method === "POST",
      ),
    ).toBe(true);
    expect(
      calls.some(
        ({ url, method }) =>
          url.endsWith("/api/v1/admin/profiling/config") && method === "PUT",
      ),
    ).toBe(true);
  });

  it("uses the recommendation list and generation contracts", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockImplementation(async (url) => {
      const isList = String(url).includes("?limit=10");
      return new Response(
        JSON.stringify(
          isList
            ? []
            : {
                id: "batch-1",
                created_at: "2026-07-16T12:00:00Z",
                recommendations: [],
              },
        ),
      );
    });

    await api.getRecommendations(10);
    await api.createRecommendations(3);

    expect(fetchMock.mock.calls[0][0]).toBe(
      "http://localhost:8021/api/v1/recommendations?limit=10",
    );
    expect(fetchMock.mock.calls[0][1]).toEqual(
      expect.objectContaining({ credentials: "include" }),
    );
    expect(fetchMock.mock.calls[1][0]).toBe(
      "http://localhost:8021/api/v1/recommendations",
    );
    expect(fetchMock.mock.calls[1][1]).toEqual(
      expect.objectContaining({
        method: "POST",
        credentials: "include",
        body: JSON.stringify({ count: 3 }),
      }),
    );
  });

  it("throws a timeout error when the request is aborted", async () => {
    const timeoutError = new Error("The operation was aborted");
    timeoutError.name = "AbortError";
    vi.spyOn(globalThis, "fetch").mockRejectedValueOnce(timeoutError);

    await expect(api.me()).rejects.toEqual(
      expect.objectContaining<ApiError>({
        status: 0,
        message: "Request timed out. Please try again.",
        name: "ApiError",
        kind: "timeout",
      }),
    );
  });

  it("throws a timeout error when AbortSignal.timeout fires", async () => {
    const timeoutError = new DOMException("signal timed out", "TimeoutError");
    vi.spyOn(globalThis, "fetch").mockRejectedValueOnce(timeoutError);

    await expect(api.me()).rejects.toEqual(
      expect.objectContaining<ApiError>({
        status: 0,
        message: "Request timed out. Please try again.",
        name: "ApiError",
        kind: "timeout",
      }),
    );
  });

  it("throws a network error when fetch fails without an abort", async () => {
    vi.spyOn(globalThis, "fetch").mockRejectedValueOnce(new TypeError("Failed to fetch"));

    await expect(api.me()).rejects.toEqual(
      expect.objectContaining<ApiError>({
        status: 0,
        message: "Network error. Check your connection and try again.",
        name: "ApiError",
        kind: "network",
      }),
    );
  });

  it("survives malformed JSON in error responses", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValueOnce(
      new Response("not-json", { status: 500 }),
    );

    await expect(api.me()).rejects.toEqual(
      expect.objectContaining<ApiError>({
        status: 500,
        message: "not-json",
        name: "ApiError",
        kind: "http",
      }),
    );
  });

  it("uses preferences, analytics, recommendation lifecycle, and observability contracts", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockImplementation(
      async () => new Response(JSON.stringify({}), { status: 200 }),
    );
    const preferences = {
      target_countries: ["TR"],
      target_cities: ["Istanbul"],
      content_languages: ["tr-TR"],
      timezone: "Europe/Istanbul",
      niches: ["Travel"],
      goals: ["Increase saves"],
      constraints: ["One-person crew"],
    };

    await api.getPreferences();
    await api.updatePreferences(preferences);
    await api.getProfileAnalytics();
    await api.createRecommendationEvent("idea 1", {
      state: "dismissed",
      reason: "not_my_style",
      idempotency_key: "event-key-1",
    });
    await api.linkRecommendationPost("idea 1", "media-1");
    await api.createRecommendationExperiment({
      recommendation_id: "idea 1",
      name: "Hook test",
      variants: ["A", "B"],
    });
    await api.updateRecommendationExperiment("experiment 1", "running");
    await api.getAdminObservability();
    await api.runOfflineEvaluation({
      model_version: "ranking-v3",
      data_cutoff: "2026-07-17T12:00:00Z",
      k: 10,
    });

    const calls = fetchMock.mock.calls.map(([url, init]) => ({
      url: String(url),
      method: init?.method ?? "GET",
      credentials: init?.credentials,
      body: init?.body,
    }));
    expect(calls).toEqual(
      expect.arrayContaining([
        expect.objectContaining({
          url: "http://localhost:8021/api/v1/preferences",
          method: "PUT",
          credentials: "include",
          body: JSON.stringify(preferences),
        }),
        expect.objectContaining({
          url: "http://localhost:8021/api/v1/profile/analytics",
          method: "GET",
        }),
        expect.objectContaining({
          url: "http://localhost:8021/api/v1/recommendations/idea%201/events",
          method: "POST",
          body: JSON.stringify({
            state: "dismissed",
            reason: "not_my_style",
            idempotency_key: "event-key-1",
          }),
        }),
        expect.objectContaining({
          url: "http://localhost:8021/api/v1/recommendation-experiments",
          method: "POST",
        }),
        expect.objectContaining({
          url: "http://localhost:8021/api/v1/admin/observability",
          method: "GET",
        }),
        expect.objectContaining({
          url: "http://localhost:8021/api/v1/admin/evaluations/run",
          method: "POST",
          body: JSON.stringify({
            model_version: "ranking-v3",
            data_cutoff: "2026-07-17T12:00:00Z",
            k: 10,
          }),
        }),
      ]),
    );
    expect(calls.every((call) => call.credentials === "include")).toBe(true);
  });
});
