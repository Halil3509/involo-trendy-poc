import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { CreatorsDashboard } from "@/components/creators-dashboard";
import { ApiError, api } from "@/lib/api";
import type { TrackedCreator } from "@/lib/types";

vi.mock("next/link", () => ({
  default: ({ href, children, ...rest }: { href: string; children: React.ReactNode }) => (
    <a href={href} {...rest}>
      {children}
    </a>
  ),
}));

vi.mock("@/lib/api", () => ({
  ApiError: class ApiError extends Error {
    constructor(message: string, public status = 0) {
      super(message);
    }
  },
  api: {
    getTrackedCreators: vi.fn(),
    addTrackedCreator: vi.fn(),
    removeTrackedCreator: vi.fn(),
  },
}));

function creator(overrides: Partial<TrackedCreator> = {}): TrackedCreator {
  return {
    id: "creator-1",
    username: "excalibur",
    display_name: "Ex Calibur",
    avatar_url: null,
    follower_count: 42000,
    media_count: 210,
    trend_score: 73.5,
    status: "active",
    last_tracked_at: "2026-07-29T00:00:00Z",
    last_error: null,
    added_at: "2026-07-20T10:00:00Z",
    ...overrides,
  };
}

describe("CreatorsDashboard", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(api.getTrackedCreators).mockResolvedValue([creator()]);
    vi.mocked(api.addTrackedCreator).mockResolvedValue(creator());
    vi.mocked(api.removeTrackedCreator).mockResolvedValue(undefined);
  });

  it("lists tracked creators with followers and trend score", async () => {
    render(<CreatorsDashboard />);

    expect(await screen.findByText("@excalibur")).toBeInTheDocument();
    expect(screen.getByText("42.0K")).toBeInTheDocument();
    expect(screen.getByText("73.5")).toBeInTheDocument();
    expect(screen.getByText("Active")).toBeInTheDocument();
  });

  it("adds a creator and refreshes the list", async () => {
    const user = userEvent.setup();
    render(<CreatorsDashboard />);
    await screen.findByText("@excalibur");

    await user.type(screen.getByLabelText("Instagram username"), "@newcreator");
    await user.click(screen.getByRole("button", { name: "Add creator" }));

    expect(api.addTrackedCreator).toHaveBeenCalledWith("newcreator");
    expect(api.getTrackedCreators).toHaveBeenCalledTimes(2);
  });

  it("shows the API error message when adding fails", async () => {
    const user = userEvent.setup();
    vi.mocked(api.addTrackedCreator).mockRejectedValue(
      new ApiError("creator not found"),
    );
    render(<CreatorsDashboard />);
    await screen.findByText("@excalibur");

    await user.type(screen.getByLabelText("Instagram username"), "ghost");
    await user.click(screen.getByRole("button", { name: "Add creator" }));

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "creator not found",
    );
  });

  it("removes a creator from the list", async () => {
    const user = userEvent.setup();
    render(<CreatorsDashboard />);
    await screen.findByText("@excalibur");

    await user.click(
      screen.getByRole("button", { name: "Stop tracking @excalibur" }),
    );

    expect(api.removeTrackedCreator).toHaveBeenCalledWith("creator-1");
    expect(screen.queryByText("@excalibur")).not.toBeInTheDocument();
  });

  it("surfaces the needs-intervention status", async () => {
    vi.mocked(api.getTrackedCreators).mockResolvedValue([
      creator({ status: "needs_intervention" }),
    ]);
    render(<CreatorsDashboard />);

    expect(
      await screen.findByText(/verification required/i),
    ).toBeInTheDocument();
  });

  it("shows an empty state when nothing is tracked", async () => {
    vi.mocked(api.getTrackedCreators).mockResolvedValue([]);
    render(<CreatorsDashboard />);

    expect(
      await screen.findByText(/No creators tracked yet/i),
    ).toBeInTheDocument();
  });
});
