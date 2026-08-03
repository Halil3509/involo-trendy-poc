import { fireEvent, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { ContentRecommendations } from "@/components/content-recommendations";
import { api } from "@/lib/api";
import type { RecommendationBatch } from "@/lib/types";

vi.mock("@/lib/api", () => ({
  api: {
    getRecommendations: vi.fn(),
    createRecommendations: vi.fn(),
    createRecommendationEvent: vi.fn(),
    linkRecommendationPost: vi.fn(),
    createRecommendationExperiment: vi.fn(),
  },
}));

const olderBatch = batch("older", "2026-07-14T09:00:00Z", "Older idea");
const latestBatch = batch("latest", "2026-07-16T09:00:00Z", "Latest idea");
const generatedBatch = batch("generated", "2026-07-16T12:00:00Z", "Generated idea");

describe("ContentRecommendations", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(api.getRecommendations).mockResolvedValue([
      latestBatch,
      olderBatch,
    ]);
    vi.mocked(api.createRecommendations).mockResolvedValue(generatedBatch);
    vi.mocked(api.createRecommendationEvent).mockImplementation(
      async (recommendationId, event) => ({
        id: "event-1",
        recommendation_id: recommendationId,
        state: event.state,
        created_at: "2026-07-17T18:00:00Z",
      }),
    );
  });

  it("loads the latest batch and keeps earlier batches in separate history", async () => {
    renderRecommendations();

    expect(screen.getByRole("status")).toHaveTextContent(
      "Loading recommendation history",
    );
    expect(await screen.findByText("Latest idea")).toBeInTheDocument();

    const latest = screen
      .getByRole("heading", { name: "Latest recommendations" })
      .closest("section");
    const history = screen
      .getByRole("heading", { name: "Recommendation history" })
      .closest("div.card");
    expect(latest).not.toBeNull();
    expect(history).not.toBeNull();
    expect(
      within(latest as HTMLElement).queryByText("Older idea"),
    ).not.toBeInTheDocument();
    expect(within(history as HTMLElement).getByText("Older idea")).toBeInTheDocument();
    expect(api.getRecommendations).toHaveBeenCalledWith(10);
  });

  it("generates three ideas and promotes the returned batch to latest", async () => {
    const user = userEvent.setup();
    renderRecommendations();
    await screen.findByText("Latest idea");

    await user.click(screen.getByRole("button", { name: "Generate 3 ideas" }));

    expect(api.createRecommendations).toHaveBeenCalledWith(3);
    expect(await screen.findByText("Generated idea")).toBeInTheDocument();
    const history = screen
      .getByRole("heading", { name: "Recommendation history" })
      .closest("div.card");
    expect(
      within(history as HTMLElement).getByText("Latest idea"),
    ).toBeInTheDocument();
  });

  it("saves a recommendation with a card-specific accessible action", async () => {
    const user = userEvent.setup();
    renderRecommendations();
    await screen.findByText("Latest idea");

    await user.click(screen.getByRole("button", { name: "Save “Latest idea”" }));

    expect(api.createRecommendationEvent).toHaveBeenCalledWith(
      "latest-recommendation",
      expect.objectContaining({ state: "saved" }),
    );
    expect(await screen.findByText("saved")).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Move “Latest idea” to production" }),
    ).toBeInTheDocument();
  });

  it("guards the generation request against rapid double clicks", async () => {
    let resolveGeneration!: (batch: RecommendationBatch) => void;
    vi.mocked(api.createRecommendations).mockReturnValue(
      new Promise((resolve) => {
        resolveGeneration = resolve;
      }),
    );
    renderRecommendations();
    await screen.findByText("Latest idea");

    const button = screen.getByRole("button", { name: "Generate 3 ideas" });
    fireEvent.click(button);
    fireEvent.click(button);

    expect(api.createRecommendations).toHaveBeenCalledOnce();
    expect(screen.getByRole("button", { name: "Generating ideas..." })).toBeDisabled();
    resolveGeneration(generatedBatch);
    expect(await screen.findByText("Generated idea")).toBeInTheDocument();
  });

  it.each([
    ["disconnected", "Connect Instagram below"],
    ["needs_reauth", "Reconnect Instagram below"],
    ["connected", "Analyze your Instagram profile below"],
    ["profiling", "still being analyzed"],
    ["failed", "Profile analysis failed"],
  ] as const)("blocks generation while profile status is %s", async (status, message) => {
    render(
      <ContentRecommendations
        instagramStatus={{ status }}
        instagramStatusLoading={false}
      />,
    );

    expect(await screen.findByText(new RegExp(message))).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Generate 3 ideas" })).toBeDisabled();
  });

  it("blocks generation while shared Instagram status is loading or failed", async () => {
    const { rerender } = render(
      <ContentRecommendations instagramStatus={null} instagramStatusLoading />,
    );

    expect(
      await screen.findByText("Checking whether your Instagram profile is ready..."),
    ).toBeInTheDocument();

    rerender(
      <ContentRecommendations
        instagramStatus={null}
        instagramStatusLoading={false}
        instagramStatusError="Network unavailable"
      />,
    );
    expect(
      screen.getByText(
        "Instagram profile status is unavailable: Network unavailable",
      ),
    ).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Generate 3 ideas" })).toBeDisabled();
  });

  it("shows an empty state when there is no history", async () => {
    vi.mocked(api.getRecommendations).mockResolvedValue([]);
    renderRecommendations();

    expect(await screen.findByText("No recommendations yet")).toBeInTheDocument();
    expect(
      screen.queryByRole("heading", { name: "Recommendation history" }),
    ).not.toBeInTheDocument();
  });

  it("surfaces history errors and retries the request", async () => {
    const user = userEvent.setup();
    vi.mocked(api.getRecommendations)
      .mockRejectedValueOnce(new Error("History unavailable"))
      .mockResolvedValueOnce([latestBatch]);
    renderRecommendations();

    expect(await screen.findByRole("alert")).toHaveTextContent("History unavailable");
    await user.click(screen.getByRole("button", { name: "Retry history" }));

    expect(await screen.findByText("Latest idea")).toBeInTheDocument();
    expect(api.getRecommendations).toHaveBeenCalledTimes(2);
  });

  it("surfaces generation errors and allows another attempt", async () => {
    const user = userEvent.setup();
    vi.mocked(api.createRecommendations)
      .mockRejectedValueOnce(new Error("Generation unavailable"))
      .mockResolvedValueOnce(generatedBatch);
    renderRecommendations();
    await screen.findByText("Latest idea");

    await user.click(screen.getByRole("button", { name: "Generate 3 ideas" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Generation unavailable",
    );
    expect(screen.getByRole("button", { name: "Generate 3 ideas" })).toBeEnabled();

    await user.click(screen.getByRole("button", { name: "Generate 3 ideas" }));
    expect(await screen.findByText("Generated idea")).toBeInTheDocument();
    expect(api.createRecommendations).toHaveBeenCalledTimes(2);
  });
});

function renderRecommendations() {
  return render(
    <ContentRecommendations
      instagramStatus={{ status: "ready" }}
      instagramStatusLoading={false}
    />,
  );
}

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
