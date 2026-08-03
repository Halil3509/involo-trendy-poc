import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { AdminObservability } from "@/components/admin-observability";
import { ApiError, api } from "@/lib/api";
import type { AdminObservability as Observability, EvaluationRun } from "@/lib/types";

vi.mock("@/lib/api", async (importOriginal) => {
  const original = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...original,
    api: {
      getAdminObservability: vi.fn(),
      runOfflineEvaluation: vi.fn(),
    },
  };
});

const thresholds = {
  min_ndcg_at_k: 0.5,
  min_precision_at_k: 0.2,
  max_brier: 0.25,
  max_p95_latency_seconds: 30,
  max_cost_per_prediction: 1,
};

const observability: Observability = {
  queue_age_seconds: null,
  job_duration_p50_seconds: 5,
  job_duration_p95_seconds: 12,
  stale_trends: 0,
  stale_profiles: 0,
  attention_jobs: 0,
  stale_jobs: 0,
  snapshot_coverage: 0.9,
  multimodal_failures: {},
  provider_usage: {
    totals: { input_tokens: 100, output_tokens: 20, estimated_cost: 0.03 },
    groups: [],
  },
  evaluation: {
    latest: null,
    thresholds: {
      ...thresholds,
      rollback_ndcg_drop: 0.1,
      rollback_precision_drop: 0.1,
      rollback_brier_increase: 0.05,
    },
  },
  funnel: {},
};

const evaluation: EvaluationRun = {
  id: "evaluation-1",
  model_version: "ranking-v3",
  data_cutoff: "2026-07-17T12:00:00Z",
  evaluation_version: "offline-ranking-v1",
  label_definition: "Later snapshot views above median",
  k: 10,
  sample_size: 12,
  candidate_sample_size: 120,
  metrics: {
    ndcg_at_k: 0.7,
    precision_at_k: 0.3,
    brier: 0.2,
    reliability_buckets: [],
    p95_latency_seconds: 10,
    cost_per_prediction: 0.05,
  },
  thresholds,
  passed: true,
  rollback_recommended: false,
  created_at: "2026-07-17T18:00:00Z",
};

describe("AdminObservability", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(api.getAdminObservability).mockResolvedValue(observability);
    vi.mocked(api.runOfflineEvaluation).mockResolvedValue(evaluation);
  });

  it("runs an offline evaluation and announces quality-gate results", async () => {
    const user = userEvent.setup();
    render(<AdminObservability />);
    await screen.findByRole("heading", { name: "Run offline evaluation" });

    await user.type(screen.getByLabelText("Model version"), "ranking-v3");
    await user.clear(screen.getByLabelText("Data cutoff"));
    await user.type(screen.getByLabelText("Data cutoff"), "2026-07-17T12:00");
    await user.click(screen.getByRole("button", { name: "Run evaluation" }));

    expect(api.runOfflineEvaluation).toHaveBeenCalledWith({
      model_version: "ranking-v3",
      data_cutoff: new Date("2026-07-17T12:00").toISOString(),
      k: 10,
    });
    expect(
      await screen.findByText("Offline evaluation passed all quality gates."),
    ).toBeInTheDocument();
    expect(screen.getByText("Quality gates passed")).toBeInTheDocument();
  });

  it("explains when historical labeled data is unavailable", async () => {
    vi.mocked(api.runOfflineEvaluation).mockRejectedValue(
      new ApiError("no labeled historical rankings are available", 409),
    );
    const user = userEvent.setup();
    render(<AdminObservability />);
    await screen.findByRole("heading", { name: "Run offline evaluation" });

    await user.type(screen.getByLabelText("Model version"), "new-model");
    await user.click(screen.getByRole("button", { name: "Run evaluation" }));

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "No historical labeled rankings are available",
    );
    expect(screen.getByRole("alert")).toHaveTextContent(
      "Collect later outcome snapshots or labels",
    );
  });
});
