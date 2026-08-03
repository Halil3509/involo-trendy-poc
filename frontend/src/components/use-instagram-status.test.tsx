import { render, screen } from "@testing-library/react";

import { useInstagramStatus } from "@/components/use-instagram-status";
import { api } from "@/lib/api";

vi.mock("@/lib/api", () => ({
  api: {
    getInstagramStatus: vi.fn(),
  },
}));

describe("useInstagramStatus", () => {
  it("loads status once and shares the same result with dashboard consumers", async () => {
    vi.mocked(api.getInstagramStatus).mockResolvedValue({
      status: "ready",
      instagram_username: "creator",
    });

    render(<DashboardConsumers />);

    expect(await screen.findByText("Profile: ready")).toBeInTheDocument();
    expect(screen.getByText("Recommendations: ready")).toBeInTheDocument();
    expect(api.getInstagramStatus).toHaveBeenCalledOnce();
  });

  it("exposes status request failures", async () => {
    vi.mocked(api.getInstagramStatus).mockRejectedValue(
      new Error("Instagram status unavailable"),
    );

    render(<DashboardConsumers />);

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Instagram status unavailable",
    );
  });
});

function DashboardConsumers() {
  const instagram = useInstagramStatus();

  if (instagram.loading) return <p>Loading</p>;
  if (instagram.error) return <p role="alert">{instagram.error}</p>;

  return (
    <>
      <p>Profile: {instagram.status?.status}</p>
      <p>Recommendations: {instagram.status?.status}</p>
    </>
  );
}
