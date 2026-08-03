import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { CreatorDetail } from "@/components/creator-detail";
import { api } from "@/lib/api";
import type {
  FollowerHistory,
  Job,
  TrackedCreatorDetail,
} from "@/lib/types";

vi.mock("@/lib/api", () => ({
  ApiError: class ApiError extends Error {
    constructor(message: string, public status = 0) {
      super(message);
    }
  },
  api: {
    getTrackedCreator: vi.fn(),
    getTrackedCreatorFollowers: vi.fn(),
    getTrackedCreatorContent: vi.fn(),
    analyzeTrackedCreator: vi.fn(),
  },
}));

vi.mock("@/components/scraper-log-console", () => ({
  ScraperLogConsole: function ScraperLogConsoleMock({
    taskId,
    creatorId,
    path,
  }: {
    taskId: string | null;
    creatorId?: string;
    path?: string;
  }) {
    return (
      <div data-testid="log-console" data-task-id={taskId} data-creator-id={creatorId} data-path={path}>
        Log console
      </div>
    );
  },
}));

const detail: TrackedCreatorDetail = {
  id: "creator-1",
  username: "excalibur",
  display_name: "Ex Calibur",
  avatar_url: null,
  follower_count: 42000,
  following_count: 350,
  media_count: 210,
  trend_score: 73.5,
  status: "active",
  last_tracked_at: "2026-07-29T00:00:00Z",
  last_error: null,
  added_at: "2026-07-20T10:00:00Z",
  bio: "Travel and food",
  ai_summary: "Travel niche creator with strong reels.",
  structured_profile: { pillars: [{ name: "travel" }] },
  average_viral_score: 55.2,
  profile_updated_at: "2026-07-29T00:00:00Z",
};

const history: FollowerHistory = {
  range: "month",
  points: [
    { captured_at: "2026-07-01T00:00:00Z", follower_count: 40000 },
    { captured_at: "2026-07-29T00:00:00Z", follower_count: 42000 },
  ],
  delta: 2000,
};

describe("CreatorDetail", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(api.getTrackedCreator).mockResolvedValue(detail);
    vi.mocked(api.getTrackedCreatorFollowers).mockResolvedValue(history);
    vi.mocked(api.getTrackedCreatorContent).mockResolvedValue({
      items: [
        {
          shortcode: "ABC1",
          permalink: "https://www.instagram.com/p/ABC1/",
          caption_text: "Sunset reel",
          media_type: "REELS",
          thumbnail_url: null,
          taken_at: "2026-07-28T00:00:00Z",
          like_count: 5200,
          comment_count: 210,
          view_count: 120000,
          viral_score: 88,
          is_new: true,
          processing_status: "embedded",
        },
      ],
      new_count: 1,
    });
    vi.mocked(api.analyzeTrackedCreator).mockResolvedValue({
      id: "job-1",
      kind: "creator_track",
      state: "queued",
      counters: {},
      created_at: "2026-07-30T00:00:00Z",
    } as Job);
  });

  it("renders the profile, AI summary, and content", async () => {
    render(<CreatorDetail creatorId="creator-1" />);

    expect(await screen.findByText("@excalibur")).toBeInTheDocument();
    expect(screen.getByText("Ex Calibur")).toBeInTheDocument();
    expect(
      screen.getByText(/Travel niche creator with strong reels/),
    ).toBeInTheDocument();
    expect(screen.getByText("73.5")).toBeInTheDocument();
    expect(await screen.findByText("Sunset reel")).toBeInTheDocument();
    expect(screen.getByText("New")).toBeInTheDocument();
  });

  it("queues an analysis when Analyze now is clicked", async () => {
    const user = userEvent.setup();
    render(<CreatorDetail creatorId="creator-1" />);
    await screen.findByText("@excalibur");

    await user.click(screen.getByRole("button", { name: "Analyze now" }));

    expect(api.analyzeTrackedCreator).toHaveBeenCalledWith("creator-1");
    expect(await screen.findByRole("status")).toHaveTextContent(/queued/i);
  });

  it("opens an accordion with live logs after Analyze now", async () => {
    const user = userEvent.setup();
    render(<CreatorDetail creatorId="creator-1" />);
    await screen.findByText("@excalibur");

    await user.click(screen.getByRole("button", { name: "Analyze now" }));

    expect(await screen.findByRole("button", { expanded: true })).toHaveTextContent(
      /Live analysis logs/i,
    );
    const console = screen.getByTestId("log-console");
    expect(console).toBeInTheDocument();
    expect(console).toHaveAttribute("data-task-id", "job-1");
    expect(console).toHaveAttribute("data-creator-id", "creator-1");
    expect(console).toHaveAttribute(
      "data-path",
      "/api/v1/creators/{creatorId}/analyze/{taskId}/logs",
    );
  });

  it("toggles the live logs accordion", async () => {
    const user = userEvent.setup();
    render(<CreatorDetail creatorId="creator-1" />);
    await screen.findByText("@excalibur");

    await user.click(screen.getByRole("button", { name: "Analyze now" }));
    await screen.findByTestId("log-console");

    const accordion = screen.getByRole("button", { expanded: true });
    await user.click(accordion);

    expect(screen.queryByTestId("log-console")).not.toBeInTheDocument();
    expect(accordion).toHaveAttribute("aria-expanded", "false");

    await user.click(accordion);

    expect(await screen.findByTestId("log-console")).toBeInTheDocument();
    expect(accordion).toHaveAttribute("aria-expanded", "true");
  });

  it("switches follower history ranges", async () => {
    const user = userEvent.setup();
    render(<CreatorDetail creatorId="creator-1" />);
    await screen.findByText("@excalibur");
    await screen.findByLabelText("Follower count over time");

    await user.click(screen.getByRole("button", { name: "Year" }));

    expect(api.getTrackedCreatorFollowers).toHaveBeenCalledWith(
      "creator-1",
      "year",
    );
  });

  it("shows a paused banner for needs-intervention creators", async () => {
    vi.mocked(api.getTrackedCreator).mockResolvedValue({
      ...detail,
      status: "needs_intervention",
    });
    render(<CreatorDetail creatorId="creator-1" />);

    expect(
      await screen.findByText(/Daily tracking is paused/i),
    ).toBeInTheDocument();
  });
});
