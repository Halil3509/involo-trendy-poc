import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { InstagramProfileCard } from "@/components/instagram-profile-card";
import { api } from "@/lib/api";
import type { InstagramStatus } from "@/lib/types";

vi.mock("@/lib/api", () => ({
  api: {
    startInstagramOAuth: vi.fn(),
    disconnectInstagram: vi.fn(),
    syncProfile: vi.fn(),
  },
}));

describe("InstagramProfileCard", () => {
  const readyStatus: InstagramStatus = {
    status: "ready",
    instagram_username: "creator",
    content_count_analyzed: 24,
    last_synced_at: "2026-07-16T12:00:00Z",
    ai_profile_summary: "Visual storytelling with a focus on sustainable style.",
    vector_std_dev: 0.125,
  };

  beforeEach(() => {
    window.history.replaceState({}, "", "/dashboard");
    vi.mocked(api.syncProfile).mockResolvedValue({
      id: "profile-job",
      kind: "profile",
      state: "queued",
      counters: {},
    });
  });

  it("renders profile details and starts an on-demand analysis", async () => {
    const user = userEvent.setup();
    const refreshStatus = vi.fn().mockResolvedValue(readyStatus);
    render(
      <InstagramProfileCard
        status={readyStatus}
        loading={false}
        refreshStatus={refreshStatus}
      />,
    );

    expect(await screen.findByText("@creator")).toBeInTheDocument();
    expect(screen.getByText("24")).toBeInTheDocument();
    expect(
      screen.getByText("Visual storytelling with a focus on sustainable style."),
    ).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Analyze now" }));

    await waitFor(() => expect(api.syncProfile).toHaveBeenCalledOnce());
    expect(refreshStatus).toHaveBeenCalledOnce();
    expect(screen.getByText("Profile analysis queued.")).toBeInTheDocument();
  });

  it("offers connection when Instagram is disconnected", async () => {
    render(
      <InstagramProfileCard
        status={{ status: "disconnected" }}
        loading={false}
        refreshStatus={vi.fn()}
      />,
    );

    expect(
      await screen.findByRole("button", { name: "Connect Instagram" }),
    ).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Analyze now" })).not.toBeInTheDocument();
  });

  it("shows the backend OAuth callback error message", async () => {
    window.history.replaceState(
      {},
      "",
      "/dashboard?instagram=error&message=Authorization%20denied",
    );

    render(
      <InstagramProfileCard
        status={readyStatus}
        loading={false}
        refreshStatus={vi.fn()}
      />,
    );

    expect(await screen.findByRole("alert")).toHaveTextContent("Authorization denied");
  });

  it("renders shared status loading and error states without fetching status", () => {
    const { rerender } = render(
      <InstagramProfileCard
        status={null}
        loading
        refreshStatus={vi.fn()}
      />,
    );

    expect(screen.getByRole("status")).toHaveTextContent("Loading Instagram profile");

    rerender(
      <InstagramProfileCard
        status={null}
        loading={false}
        statusError="Status unavailable"
        refreshStatus={vi.fn()}
      />,
    );
    expect(screen.getByRole("alert")).toHaveTextContent("Status unavailable");
  });

  it("opens a popup with the Instagram authorization URL on connect", async () => {
    const user = userEvent.setup();
    const fakeWindow = { closed: false, close: vi.fn() } as unknown as Window;
    const openSpy = vi.spyOn(window, "open").mockReturnValue(fakeWindow);
    vi.mocked(api.startInstagramOAuth).mockResolvedValue({
      authorization_url: "https://instagram.test/oauth",
    });

    render(
      <InstagramProfileCard
        status={{ status: "disconnected" }}
        loading={false}
        refreshStatus={vi.fn()}
      />,
    );

    await user.click(screen.getByRole("button", { name: "Connect Instagram" }));

    await waitFor(() => expect(api.startInstagramOAuth).toHaveBeenCalledOnce());
    expect(openSpy).toHaveBeenCalledOnce();
    expect(openSpy).toHaveBeenCalledWith(
      "https://instagram.test/oauth",
      "instagram_oauth",
      expect.stringMatching(/popup/),
    );
    expect(openSpy).toHaveBeenCalledWith(
      "https://instagram.test/oauth",
      "instagram_oauth",
      expect.stringMatching(/width=520/),
    );
  });

  it("shows an error when the browser blocks the popup", async () => {
    const user = userEvent.setup();
    vi.spyOn(window, "open").mockReturnValue(null);
    vi.mocked(api.startInstagramOAuth).mockResolvedValue({
      authorization_url: "https://instagram.test/oauth",
    });

    render(
      <InstagramProfileCard
        status={{ status: "disconnected" }}
        loading={false}
        refreshStatus={vi.fn()}
      />,
    );

    await user.click(screen.getByRole("button", { name: "Connect Instagram" }));

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Please allow popups",
    );
  });

  it("refreshes status and shows a success message when the popup reports a connection", async () => {
    const user = userEvent.setup();
    const refreshStatus = vi.fn().mockResolvedValue({ status: "connected" });
    const fakeWindow = { closed: false, close: vi.fn() } as unknown as Window;
    vi.spyOn(window, "open").mockReturnValue(fakeWindow);
    vi.mocked(api.startInstagramOAuth).mockResolvedValue({
      authorization_url: "https://instagram.test/oauth",
    });

    render(
      <InstagramProfileCard
        status={{ status: "disconnected" }}
        loading={false}
        refreshStatus={refreshStatus}
      />,
    );

    await user.click(screen.getByRole("button", { name: "Connect Instagram" }));

    await act(async () => {
      window.dispatchEvent(
        new MessageEvent("message", {
          origin: window.location.origin,
          data: { type: "involo:instagram:oauth", status: "connected" },
        }),
      );
    });

    await waitFor(() => expect(refreshStatus).toHaveBeenCalled());
    expect(fakeWindow.close).toHaveBeenCalled();
    expect(screen.getByRole("status")).toHaveTextContent(
      "Instagram account connected successfully.",
    );
  });

  it("shows an error when the popup reports an OAuth failure", async () => {
    const user = userEvent.setup();
    const refreshStatus = vi.fn().mockResolvedValue({ status: "disconnected" });
    const fakeWindow = { closed: false, close: vi.fn() } as unknown as Window;
    vi.spyOn(window, "open").mockReturnValue(fakeWindow);
    vi.mocked(api.startInstagramOAuth).mockResolvedValue({
      authorization_url: "https://instagram.test/oauth",
    });

    render(
      <InstagramProfileCard
        status={{ status: "disconnected" }}
        loading={false}
        refreshStatus={refreshStatus}
      />,
    );

    await user.click(screen.getByRole("button", { name: "Connect Instagram" }));

    await act(async () => {
      window.dispatchEvent(
        new MessageEvent("message", {
          origin: window.location.origin,
          data: {
            type: "involo:instagram:oauth",
            status: "error",
            message: "User denied",
          },
        }),
      );
    });

    await waitFor(() => expect(refreshStatus).toHaveBeenCalled());
    expect(fakeWindow.close).toHaveBeenCalled();
    expect(screen.getByRole("alert")).toHaveTextContent("User denied");
  });
});
