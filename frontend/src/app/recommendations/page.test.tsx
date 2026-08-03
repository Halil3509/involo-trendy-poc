import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ReactNode } from "react";
import { vi } from "vitest";

import RecommendationsPage from "@/app/recommendations/page";
import { api } from "@/lib/api";
import type { RecommendationBatch, RecommendationState } from "@/lib/types";

vi.mock("@/components/app-shell", () => ({
  AppShell: ({ children }: { children: ReactNode }) => (
    <div data-testid="app-shell">{children}</div>
  ),
}));

vi.mock("@/components/use-instagram-status", () => ({
  useInstagramStatus: vi.fn().mockReturnValue({
    status: { status: "ready" },
    loading: false,
    error: "",
    refresh: vi.fn(),
  }),
}));

vi.mock("@/lib/api", () => ({
  api: {
    getRecommendations: vi.fn(),
    createRecommendations: vi.fn(),
    createRecommendationEvent: vi.fn(),
    linkRecommendationPost: vi.fn(),
    createRecommendationExperiment: vi.fn(),
  },
}));

const latestBatch = batch("latest", "2026-07-16T09:00:00Z", "Latest idea");
const generatedBatch = batch("generated", "2026-07-16T12:00:00Z", "Generated idea");

describe("RecommendationsPage", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(api.getRecommendations).mockResolvedValue([latestBatch]);
    vi.mocked(api.createRecommendations).mockResolvedValue(generatedBatch);
    vi.mocked(api.createRecommendationEvent).mockImplementation(
      async (recommendationId: string, event: { state: RecommendationState }) => ({
        id: "event-1",
        recommendation_id: recommendationId,
        state: event.state,
        created_at: "2026-07-17T18:00:00Z",
      }),
    );
  });

  it("renders the focused recommendations page and loads the latest batch", async () => {
    render(<RecommendationsPage />);

    expect(
      screen.getByRole("heading", { name: "Content recommendations" }),
    ).toBeInTheDocument();
    expect(await screen.findByText("Latest idea")).toBeInTheDocument();
    expect(api.getRecommendations).toHaveBeenCalledWith(10);
  });

  it("generates a new batch and promotes it to latest", async () => {
    const user = userEvent.setup();
    render(<RecommendationsPage />);

    await screen.findByText("Latest idea");
    await user.click(screen.getByRole("button", { name: "Generate 3 ideas" }));

    expect(api.createRecommendations).toHaveBeenCalledWith(3);
    expect(await screen.findByText("Generated idea")).toBeInTheDocument();
  });

  it("saves a recommendation from the focused page", async () => {
    const user = userEvent.setup();
    render(<RecommendationsPage />);

    await screen.findByText("Latest idea");
    await user.click(screen.getByRole("button", { name: "Save “Latest idea”" }));

    expect(api.createRecommendationEvent).toHaveBeenCalledWith(
      "latest-recommendation",
      expect.objectContaining({ state: "saved" }),
    );
    expect(await screen.findByText("saved")).toBeInTheDocument();
  });
});

function batch(
  id: string,
  createdAt: string,
  title: string,
): RecommendationBatch {
  return {
    id,
    created_at: createdAt,
    recommendations: [
      {
        id: `${id}-recommendation`,
        title,
        hook: `${title} hook`,
        cta: `${title} CTA`,
        content_format: "reels",
        reasoning: `${title} reasoning`,
      },
    ],
  };
}
