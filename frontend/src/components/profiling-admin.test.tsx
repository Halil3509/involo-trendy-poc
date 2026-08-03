import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { ProfilingAdmin } from "@/components/profiling-admin";
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
  api: {
    getProfilingConfig: vi.fn(),
    updateProfilingConfig: vi.fn(),
    getProfilingEstimate: vi.fn(),
    startProfilingRun: vi.fn(),
    getLatestProfilingRun: vi.fn(),
  },
}));

describe("ProfilingAdmin", () => {
  beforeEach(() => {
    vi.mocked(api.getProfilingConfig).mockResolvedValue({
      enabled: true,
      schedule_cron: "0 3 * * *",
    });
    vi.mocked(api.getProfilingEstimate).mockResolvedValue({
      connected_users: 12,
      average_seconds_per_user: 5,
      estimated_duration_seconds: 60,
      estimated_start_at: "2026-07-17T03:00:00Z",
      estimated_finish_at: "2026-07-17T03:01:00Z",
    });
    vi.mocked(api.getLatestProfilingRun).mockResolvedValue({
      id: "profiling123",
      kind: "profile",
      state: "succeeded",
      counters: { profiled: 11, failed: 1 },
    });
    vi.mocked(api.updateProfilingConfig).mockImplementation(async (config) => config);
    vi.mocked(api.startProfilingRun).mockResolvedValue({
      id: "new-run",
      kind: "profile",
      state: "queued",
      counters: {},
    });
  });

  it("loads capacity and saves a valid schedule", async () => {
    const user = userEvent.setup();
    render(<ProfilingAdmin />);

    expect(
      await screen.findByRole("heading", { name: "Capacity estimate" }),
    ).toBeInTheDocument();
    expect(screen.getByText("12")).toBeInTheDocument();

    const cron = screen.getByLabelText("Schedule (cron)");
    await user.clear(cron);
    await user.type(cron, "*/30 * * * *");
    await user.click(screen.getByRole("button", { name: "Save schedule" }));

    await waitFor(() =>
      expect(api.updateProfilingConfig).toHaveBeenCalledWith({
        enabled: true,
        schedule_cron: "*/30 * * * *",
      }),
    );
  });

  it("prevents saving an invalid cron and starts a manual run", async () => {
    const user = userEvent.setup();
    render(<ProfilingAdmin />);
    await screen.findByRole("heading", { name: "Schedule" });

    const cron = screen.getByLabelText("Schedule (cron)");
    await user.clear(cron);
    await user.type(cron, "90 * * * *");
    expect(screen.getByText("Enter a valid five-field cron expression.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Save schedule" })).toBeDisabled();

    await user.click(screen.getByRole("button", { name: "Run profiling now" }));
    await waitFor(() => expect(api.startProfilingRun).toHaveBeenCalledOnce());
    expect(screen.getByText("Profiling run started.")).toBeInTheDocument();
  });
});
