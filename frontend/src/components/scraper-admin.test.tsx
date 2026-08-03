import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { ScraperAdmin } from "@/components/scraper-admin";
import { api } from "@/lib/api";

vi.mock("@/lib/api", () => ({
  ApiError: class ApiError extends Error {
    constructor(
      message: string,
      public status: number,
    ) {
      super(message);
    }
  },
  wsUrl: (path: string) => `ws://localhost:8021${path}`,
  api: {
    getScraperConfig: vi.fn(),
    updateScraperConfig: vi.fn(),
    startScraperRun: vi.fn(),
    getLatestScraperRun: vi.fn(),
    getScraperRun: vi.fn(),
    startPipeline: vi.fn(),
    startFullPipeline: vi.fn(),
    getLatestPipelineRun: vi.fn(),
    getPipelineStats: vi.fn(),
    getAdminJobs: vi.fn(),
    stopJob: vi.fn(),
  },
}));

describe("ScraperAdmin", () => {
  beforeEach(() => {
    vi.mocked(api.getScraperConfig).mockResolvedValue({
      keywords: ["fashion"],
      reels_per_keyword: 12,
      headless: true,
      viral_threshold: 20,
      schedule_cron: "0 5 * * *",
      schedule_pipeline: false,
    });
    vi.mocked(api.getLatestScraperRun).mockResolvedValue({
      id: "abcdef123456",
      kind: "scrape",
      state: "succeeded",
      counters: { discovered: 18, inserted: 15 },
    });
    vi.mocked(api.getPipelineStats).mockResolvedValue({
      discovered: 5,
      enriched: 3,
      stored: 1,
      embedded: 2,
      needs_intervention: 0,
      failed: 0,
    });
    vi.mocked(api.getLatestPipelineRun).mockResolvedValue({
      id: "pipeline12345",
      kind: "enrich",
      state: "succeeded",
      counters: { processed: 3, scored: 3 },
    });
    vi.mocked(api.getAdminJobs).mockResolvedValue([]);
    vi.mocked(api.updateScraperConfig).mockImplementation(async (config) => config);
    vi.mocked(api.startPipeline).mockResolvedValue({
      id: "newpipeline1",
      kind: "embed",
      state: "queued",
      counters: {},
    });
    vi.mocked(api.startFullPipeline).mockResolvedValue({
      id: "newpipeline2",
      kind: "pipeline",
      state: "queued",
      counters: {},
    });
  });

  it("loads config, adds a keyword, and saves the payload", async () => {
    const user = userEvent.setup();
    render(<ScraperAdmin />);

    expect(
      await screen.findByRole("heading", { name: "Configuration" }),
    ).toBeInTheDocument();
    expect(screen.getByText("fashion")).toBeInTheDocument();
    expect(screen.getByText("18")).toBeInTheDocument();

    await user.type(
      screen.getByLabelText("Discovery keywords"),
      "sustainable style{Enter}",
    );
    expect(screen.getByText("sustainable style")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Save configuration" }));

    await waitFor(() =>
      expect(api.updateScraperConfig).toHaveBeenCalledWith(
        expect.objectContaining({
          keywords: ["fashion", "sustainable style"],
          reels_per_keyword: 12,
          headless: true,
          viral_threshold: 20,
          schedule_cron: "0 5 * * *",
        }),
      ),
    );
    expect(screen.getByText("Scraper configuration saved.")).toBeInTheDocument();
  });

  it("triggers a pipeline stage", async () => {
    const user = userEvent.setup();
    render(<ScraperAdmin />);

    await screen.findByRole("heading", { name: "Processing pipeline" });
    await user.click(screen.getByRole("button", { name: "Embed" }));

    await waitFor(() =>
      expect(api.startPipeline).toHaveBeenCalledWith("embed"),
    );
    expect(screen.getByText("Embed job started.")).toBeInTheDocument();
  });

  it("triggers the full pipeline run", async () => {
    const user = userEvent.setup();
    render(<ScraperAdmin />);

    await screen.findByRole("heading", { name: "Processing pipeline" });
    await user.click(screen.getByRole("button", { name: "Run full pipeline" }));

    await waitFor(() =>
      expect(api.startFullPipeline).toHaveBeenCalled(),
    );
    expect(screen.getByText("Full pipeline run started.")).toBeInTheDocument();
  });

  it("stops an active scrape run", async () => {
    const user = userEvent.setup();
    vi.mocked(api.stopJob).mockResolvedValue({
      id: "abcdef123456",
      kind: "scrape",
      state: "cancelled",
      counters: {},
    });
    vi.mocked(api.getLatestScraperRun).mockResolvedValue({
      id: "abcdef123456",
      kind: "scrape",
      state: "running",
      counters: { discovered: 5 },
    });

    render(<ScraperAdmin />);

    await screen.findByRole("button", { name: "Stop" });
    await user.click(screen.getByRole("button", { name: "Stop" }));

    await waitFor(() =>
      expect(api.stopJob).toHaveBeenCalledWith("abcdef123456"),
    );
    expect(screen.getByText("Stop requested for job abcdef12.")).toBeInTheDocument();
  });

  it("stops an active pipeline run", async () => {
    const user = userEvent.setup();
    vi.mocked(api.stopJob).mockResolvedValue({
      id: "pipeline12345",
      kind: "enrich",
      state: "cancelled",
      counters: {},
    });
    vi.mocked(api.getLatestPipelineRun).mockResolvedValue({
      id: "pipeline12345",
      kind: "enrich",
      state: "running",
      counters: { processed: 1 },
    });

    render(<ScraperAdmin />);

    await screen.findByRole("button", { name: "Stop enrich job" });
    await user.click(screen.getByRole("button", { name: "Stop enrich job" }));

    await waitFor(() =>
      expect(api.stopJob).toHaveBeenCalledWith("pipeline12345"),
    );
  });
});
