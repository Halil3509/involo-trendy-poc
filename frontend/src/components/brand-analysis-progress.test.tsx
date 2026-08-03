import { render, screen } from "@testing-library/react";

import { BrandAnalysisProgress } from "@/components/brand-analysis-progress";

function mockJob(state: string, counters = {}) {
  return {
    id: "job-1",
    kind: "brand_analysis",
    state,
    counters: { resolved: 1, fetched: 2, analyzed: 2, failed: 0, requested: 2, total: 2, ...counters },
    created_at: "2026-01-01T00:00:00Z",
    started_at: "2026-01-01T00:00:00Z",
    target_username: "markaadi",
  };
}

describe("BrandAnalysisProgress", () => {
  it("shows reporting phase label and 95 percent while generating the report", () => {
    render(<BrandAnalysisProgress job={mockJob("reporting")} />);

    expect(screen.getByText("Marka raporu hazırlanıyor")).toBeInTheDocument();
    expect(screen.getByText("%95")).toBeInTheDocument();
  });

  it("shows finished label when analysis succeeded", () => {
    render(<BrandAnalysisProgress job={mockJob("succeeded")} />);

    expect(screen.getByText("Tamamlandı")).toBeInTheDocument();
    expect(screen.getByText("%100")).toBeInTheDocument();
  });
});
