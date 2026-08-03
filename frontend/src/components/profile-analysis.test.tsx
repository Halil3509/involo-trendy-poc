import { render, screen } from "@testing-library/react";

import { ProfileAnalysis } from "@/components/profile-analysis";
import { api } from "@/lib/api";

vi.mock("@/lib/api", () => ({
  api: {
    getInstagramStatus: vi.fn(),
    getRecommendations: vi.fn(),
    getProfileAnalytics: vi.fn(),
    connectInstagram: vi.fn(),
    disconnectInstagram: vi.fn(),
    syncInstagram: vi.fn(),
    getInstagramSyncRun: vi.fn(),
  },
}));

describe("ProfileAnalysis", () => {
  beforeEach(() => {
    vi.mocked(api.getInstagramStatus).mockResolvedValue({
      status: "ready",
      instagram_username: "creator",
      content_count_analyzed: 42,
      vector_std_dev: 0.32,
      last_synced_at: "2026-07-16T10:00:00Z",
      ai_profile_summary: "Warm travel storyteller.",
    });
    vi.mocked(api.getRecommendations).mockResolvedValue([
      {
        id: "batch-1",
        created_at: "2026-07-16T10:00:00Z",
        recommendations: [
          {
            id: "rec-1",
            title: "Hidden coastal towns",
            hook: "Show the view first.",
            cta: "Comment your favorite spot.",
            content_format: "reels",
            reasoning: "Fits your travel focus.",
          },
        ],
      },
    ]);
    vi.mocked(api.getProfileAnalytics).mockResolvedValue({
      schema_version: "creator-profile-v2",
      pillars: [{
        id: "travel",
        name: "Travel tips",
        description: "Practical destination guidance.",
        content_count: 20,
        average_performance_residual: 0.18,
        strengths: ["Clear openings"],
        opportunities: ["Stronger CTAs"],
        confidence: 0.9,
      }],
      winning_patterns: ["Fast visual hooks"],
      losing_patterns: ["Long intros"],
      audience_markets: ["TR"],
      avoid_patterns: ["Unverified claims"],
      data_quality: 0.9,
    });
  });

  it("renders profile analytics, pillars, and recommendation history", async () => {
    render(<ProfileAnalysis />);

    expect(
      await screen.findByRole("heading", { name: "Your creator profile" }),
    ).toBeInTheDocument();

    expect(
      await screen.findByRole(
        "heading",
        { name: "Profile analytics" },
        { timeout: 3_000 },
      ),
    ).toBeInTheDocument();
    expect(screen.getByText("Travel tips")).toBeInTheDocument();
    expect(screen.getAllByText(/90%/).length).toBeGreaterThanOrEqual(1);

    expect(
      await screen.findByText("Hidden coastal towns"),
    ).toBeInTheDocument();
  });
});
