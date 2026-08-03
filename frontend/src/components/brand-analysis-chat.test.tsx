import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { vi } from "vitest";

import { BrandAnalysisChat } from "@/components/brand-analysis-chat";
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
    startBrandAnalysis: vi.fn(),
    getBrandAnalysisJob: vi.fn(),
    getBrandAnalysisReport: vi.fn(),
    getBrandAnalysisPosts: vi.fn(),
    stopJob: vi.fn(),
    exportBrandAnalysisPdf: vi.fn(),
  },
}));

const mockReport = {
  schema_version: "brand-analysis-report-v1",
  job_id: "job-1",
  markdown_text: "# Marka Raporu\n\n- Özet",
  report_s3_key: "reports/brand/job-1/report.md",
  pdf_s3_key: null,
};

const mockJob = (state: string, id = "job-1") => ({
  id,
  kind: "brand_analysis",
  state,
  counters: { resolved: 1, fetched: 2, analyzed: 2, failed: 0, requested: 2, total: 2 },
  created_at: "2026-01-01T00:00:00Z",
  started_at: "2026-01-01T00:00:00Z",
  target_username: "markaadi",
});

describe("BrandAnalysisChat", () => {
  beforeEach(() => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    vi.clearAllMocks();
    vi.mocked(api.startBrandAnalysis).mockResolvedValue(mockJob("queued"));
    vi.mocked(api.getBrandAnalysisJob).mockResolvedValue(mockJob("succeeded"));
    vi.mocked(api.getBrandAnalysisReport).mockResolvedValue(mockReport);
    vi.mocked(api.getBrandAnalysisPosts).mockResolvedValue([]);
    vi.mocked(api.exportBrandAnalysisPdf).mockResolvedValue(new Blob(["pdf"]));
    globalThis.URL.createObjectURL = vi.fn(() => "blob:mock");
    globalThis.URL.revokeObjectURL = vi.fn();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("renders input, max posts field and submit button", () => {
    render(<BrandAnalysisChat />);

    expect(
      screen.getByLabelText("Instagram kullanıcı adı veya URL"),
    ).toBeInTheDocument();
    expect(screen.getByLabelText("Max gönderi")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Analiz et" })).toBeInTheDocument();
  });

  it("starts analysis when submitted", async () => {
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
    render(<BrandAnalysisChat />);

    await user.type(
      screen.getByLabelText("Instagram kullanıcı adı veya URL"),
      "markaadi",
    );
    await user.click(screen.getByRole("button", { name: "Analiz et" }));

    await waitFor(() =>
      expect(api.startBrandAnalysis).toHaveBeenCalledWith({
        username_or_url: "markaadi",
        max_posts: 10,
      }),
    );
    expect(screen.getByText("queued")).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Analiz durumu" })).toBeInTheDocument();
    expect(screen.getByText(/@markaadi için ilerleme/)).toBeInTheDocument();
    expect(screen.getByText("Sıraya alındı")).toBeInTheDocument();
  });

  it("accepts a full Instagram profile URL", async () => {
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
    render(<BrandAnalysisChat />);

    await user.type(
      screen.getByLabelText("Instagram kullanıcı adı veya URL"),
      "https://www.instagram.com/involo.tr",
    );
    await user.click(screen.getByRole("button", { name: "Analiz et" }));

    await waitFor(() =>
      expect(api.startBrandAnalysis).toHaveBeenCalledWith({
        username_or_url: "https://www.instagram.com/involo.tr",
        max_posts: 10,
      }),
    );
    expect(screen.getByText("queued")).toBeInTheDocument();
  });

  it("accepts an @handle", async () => {
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
    render(<BrandAnalysisChat />);

    await user.type(
      screen.getByLabelText("Instagram kullanıcı adı veya URL"),
      "@involo.tr",
    );
    await user.click(screen.getByRole("button", { name: "Analiz et" }));

    await waitFor(() =>
      expect(api.startBrandAnalysis).toHaveBeenCalledWith({
        username_or_url: "@involo.tr",
        max_posts: 10,
      }),
    );
    expect(screen.getByText("queued")).toBeInTheDocument();
  });

  it("shows validation error for an invalid Instagram URL", async () => {
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
    render(<BrandAnalysisChat />);

    await user.type(
      screen.getByLabelText("Instagram kullanıcı adı veya URL"),
      "https://not-instagram.com/involo.tr",
    );
    await user.click(screen.getByRole("button", { name: "Analiz et" }));

    expect(await screen.findByText("Enter a valid Instagram username or profile URL.")).toBeInTheDocument();
    expect(api.startBrandAnalysis).not.toHaveBeenCalled();
  });

  it("displays error when start request fails", async () => {
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
    vi.mocked(api.startBrandAnalysis).mockRejectedValue(new Error("Network error"));
    render(<BrandAnalysisChat />);

    await user.type(
      screen.getByLabelText("Instagram kullanıcı adı veya URL"),
      "markaadi",
    );
    await user.click(screen.getByRole("button", { name: "Analiz et" }));

    expect(await screen.findByText("Network error")).toBeInTheDocument();
  });

  it("shows stop button while running and calls stopJob when clicked", async () => {
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
    vi.mocked(api.startBrandAnalysis).mockResolvedValue(mockJob("fetching"));
    vi.mocked(api.stopJob).mockResolvedValue(mockJob("cancelled"));
    render(<BrandAnalysisChat />);

    await user.type(
      screen.getByLabelText("Instagram kullanıcı adı veya URL"),
      "markaadi",
    );
    await user.click(screen.getByRole("button", { name: "Analiz et" }));

    await waitFor(() =>
      expect(api.startBrandAnalysis).toHaveBeenCalledWith({
        username_or_url: "markaadi",
        max_posts: 10,
      }),
    );

    const stopButton = await screen.findByRole("button", {
      name: "Analizi durdur",
    });
    expect(stopButton).toBeInTheDocument();
    await user.click(stopButton);

    await waitFor(() =>
      expect(api.stopJob).toHaveBeenCalledWith("job-1"),
    );
  });

  it("polls job status and displays report when succeeded", async () => {
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
    render(<BrandAnalysisChat />);

    await user.type(
      screen.getByLabelText("Instagram kullanıcı adı veya URL"),
      "markaadi",
    );
    await user.click(screen.getByRole("button", { name: "Analiz et" }));

    await waitFor(() =>
      expect(api.startBrandAnalysis).toHaveBeenCalledWith({
        username_or_url: "markaadi",
        max_posts: 10,
      }),
    );

    await act(async () => {
      await vi.advanceTimersByTimeAsync(3000);
    });

    await waitFor(() =>
      expect(api.getBrandAnalysisReport).toHaveBeenCalledWith("job-1"),
    );

    expect(
      await screen.findByRole("heading", { name: "Rapor" }),
    ).toBeInTheDocument();
    expect(screen.getByText(/Marka Raporu/)).toBeInTheDocument();
  });

  it("calls exportBrandAnalysisPdf and downloads when pdf button is clicked", async () => {
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
    render(<BrandAnalysisChat />);

    await user.type(
      screen.getByLabelText("Instagram kullanıcı adı veya URL"),
      "markaadi",
    );
    await user.click(screen.getByRole("button", { name: "Analiz et" }));

    await waitFor(() =>
      expect(api.startBrandAnalysis).toHaveBeenCalledWith({
        username_or_url: "markaadi",
        max_posts: 10,
      }),
    );

    await act(async () => {
      await vi.advanceTimersByTimeAsync(3000);
    });

    await waitFor(() =>
      expect(api.getBrandAnalysisReport).toHaveBeenCalledWith("job-1"),
    );

    const pdfButton = await screen.findByRole("button", {
      name: "PDF olarak indir",
    });
    await user.click(pdfButton);

    await waitFor(() =>
      expect(api.exportBrandAnalysisPdf).toHaveBeenCalledWith("job-1"),
    );
    expect(screen.getByText("PDF İndirildi")).toBeInTheDocument();
  });
});
