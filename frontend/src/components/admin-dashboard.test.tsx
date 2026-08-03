import { render, screen } from "@testing-library/react";

import { AdminDashboard } from "@/components/admin-dashboard";
import { api } from "@/lib/api";

vi.mock("@/lib/api", () => ({
  api: {
    getAdminOverview: vi.fn(),
    getAdminJobs: vi.fn(),
    getAdminObservability: vi.fn(),
    runOfflineEvaluation: vi.fn(),
  },
}));

describe("AdminDashboard", () => {
  beforeEach(() => {
    vi.mocked(api.getAdminOverview).mockResolvedValue({
      total_users: 7,
      admin_users: 2,
      connected_instagram: 4,
      needs_reauth: 1,
      trend_content_total: 30,
      pipeline: {
        discovered: 3,
        enriched: 5,
        stored: 2,
        embedded: 8,
        needs_intervention: 1,
        failed: 2,
      },
      user_content_total: 15,
      user_profiles_ready: 3,
      recommendation_batches: 9,
      jobs_by_state: { succeeded: 5, failed: 1 },
      attention_jobs: 1,
    });
    vi.mocked(api.getAdminJobs).mockResolvedValue([
      {
        id: "job-ok",
        kind: "scrape",
        state: "succeeded",
        counters: { discovered: 10 },
        created_at: "2026-07-16T10:00:00Z",
      },
      {
        id: "job-bad",
        kind: "enrich",
        state: "failed",
        counters: {},
        error: "boom",
        created_at: "2026-07-16T11:00:00Z",
      },
    ]);
    vi.mocked(api.getAdminObservability).mockResolvedValue({
      queue_age_seconds: 30,
      job_duration_p50_seconds: 8,
      job_duration_p95_seconds: 20,
      stale_trends: 2,
      stale_profiles: 1,
      attention_jobs: 1,
      stale_jobs: 0,
      snapshot_coverage: 0.86,
      multimodal_failures: { PROVIDER_TIMEOUT: 1 },
      provider_usage: {
        totals: {
          input_tokens: 1200,
          output_tokens: 300,
          estimated_cost: 0.42,
        },
        groups: [
          {
            provider: "bedrock",
            model_id: "nova-pro-v1",
            stage: "vision",
            runs: 8,
            failures: 1,
            media_seconds: 240,
            average_duration_ms: 1250,
          },
        ],
      },
      evaluation: {
        latest: {
          _id: "evaluation-1",
          model_version: "ranking-v2",
          data_cutoff: "2026-07-16T00:00:00Z",
          evaluation_version: "offline-ranking-v1",
          label_definition: "Later snapshot views above median",
          k: 10,
          sample_size: 20,
          candidate_sample_size: 200,
          metrics: {
            ndcg_at_k: 0.72,
            precision_at_k: 0.31,
            brier: 0.18,
            reliability_buckets: [],
            p95_latency_seconds: 12,
            cost_per_prediction: 0.04,
          },
          thresholds: {
            min_ndcg_at_k: 0.5,
            min_precision_at_k: 0.2,
            max_brier: 0.25,
            max_p95_latency_seconds: 30,
            max_cost_per_prediction: 1,
          },
          passed: true,
          rollback_recommended: false,
          created_at: "2026-07-17T18:00:00Z",
        },
        thresholds: {
          min_ndcg_at_k: 0.5,
          min_precision_at_k: 0.2,
          max_brier: 0.25,
          max_p95_latency_seconds: 30,
          max_cost_per_prediction: 1,
          rollback_ndcg_drop: 0.1,
          rollback_precision_drop: 0.1,
          rollback_brier_increase: 0.05,
        },
      },
      funnel: { generated: 21, saved: 6, published: 1 },
    });
  });

  it("renders overview metrics, jobs, and attention items", async () => {
    render(<AdminDashboard />);

    expect(
      await screen.findByRole("heading", { name: "Operations overview" }),
    ).toBeInTheDocument();

    // Key metric tiles
    expect(screen.getByText("Users")).toBeInTheDocument();
    expect(screen.getByText("7")).toBeInTheDocument();
    expect(screen.getByText("1 need reauth")).toBeInTheDocument();

    // Recent jobs table
    expect(screen.getByText("scrape")).toBeInTheDocument();
    // "enrich" appears in both the jobs table and the attention panel
    expect(screen.getAllByText("enrich").length).toBeGreaterThanOrEqual(1);

    // Attention panel surfaces the failed job error
    expect(screen.getByText("boom")).toBeInTheDocument();
    expect(
      screen.getByText("1 Instagram connection(s) need re-authentication."),
    ).toBeInTheDocument();
    expect(await screen.findByText(/provider timeout/i)).toBeInTheDocument();
    expect(screen.getByText("21")).toBeInTheDocument();
    expect(screen.getByText("nova-pro-v1")).toBeInTheDocument();
    expect(screen.getByText("Quality gates passed")).toBeInTheDocument();
    expect(screen.getByText(/Rollback not recommended/)).toBeInTheDocument();
  });
});
