import { act, fireEvent, render, screen } from "@testing-library/react";

import { ScraperLogConsole } from "@/components/scraper-log-console";

vi.mock("@/lib/api", () => ({
  wsUrl: (path: string) => `ws://localhost:8021${path}`,
}));

type Handler = ((event: unknown) => void) | null;

class MockWebSocket {
  static last: MockWebSocket | null = null;
  url: string;
  readyState = 0;
  onopen: Handler = null;
  onmessage: Handler = null;
  onclose: Handler = null;
  onerror: Handler = null;
  constructor(url: string) {
    this.url = url;
    MockWebSocket.last = this;
  }
  send() {}
  close() {
    this.readyState = 3;
    this.onclose?.({});
  }
}

describe("ScraperLogConsole", () => {
  beforeEach(() => {
    MockWebSocket.last = null;
    vi.stubGlobal("WebSocket", MockWebSocket);
  });

  it("shows an idle message with no task id", () => {
    render(<ScraperLogConsole taskId={null} />);
    expect(screen.getByText("No active run.")).toBeInTheDocument();
    expect(MockWebSocket.last).toBeNull();
  });

  it("streams incoming events and reflects connection state", () => {
    render(<ScraperLogConsole taskId="task-42" />);

    const socket = MockWebSocket.last;
    expect(socket).not.toBeNull();
    expect(socket?.url).toBe(
      "ws://localhost:8021/api/v1/admin/scraper/runs/task-42/logs",
    );

    act(() => {
      socket?.onopen?.({});
    });
    expect(screen.getByText("open")).toBeInTheDocument();

    act(() => {
      socket?.onmessage?.({
        data: JSON.stringify({
          ts: "2026-07-16T10:00:00Z",
          level: "success",
          step: "done",
          message: "Scrape finished.",
        }),
      });
    });

    expect(screen.getByText("Scrape finished.")).toBeInTheDocument();
    expect(screen.getByText("done")).toBeInTheDocument();
  });

  it("uses a custom path when provided", () => {
    render(
      <ScraperLogConsole
        taskId="task-99"
        title="Live pipeline log"
        path="/api/v1/admin/pipeline/runs/{taskId}/logs"
        idleMessage="Start the pipeline."
      />,
    );

    const socket = MockWebSocket.last;
    expect(socket).not.toBeNull();
    expect(socket?.url).toBe(
      "ws://localhost:8021/api/v1/admin/pipeline/runs/task-99/logs",
    );
  });

  it("clears events when the clear button is clicked", () => {
    render(<ScraperLogConsole taskId="task-42" />);

    const socket = MockWebSocket.last;
    act(() => {
      socket?.onmessage?.({
        data: JSON.stringify({
          ts: "2026-07-16T10:00:00Z",
          level: "info",
          message: "Event one.",
        }),
      });
    });

    expect(screen.getByText("Event one.")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Clear log" }));
    expect(screen.queryByText("Event one.")).not.toBeInTheDocument();
  });

  it("renders error data in full", () => {
    const longError = "x".repeat(200);
    render(<ScraperLogConsole taskId="task-42" />);

    const socket = MockWebSocket.last;
    act(() => {
      socket?.onmessage?.({
        data: JSON.stringify({
          ts: "2026-07-16T10:00:00Z",
          level: "error",
          message: "Upload failed.",
          data: { reason: longError },
        }),
      });
    });

    expect(screen.getByText(/Upload failed/)).toBeInTheDocument();
    expect(screen.getByText(new RegExp(longError))).toBeInTheDocument();
  });
});
