"use client";

import { useEffect, useRef, useState } from "react";

import { wsUrl } from "@/lib/api";
import type { ScraperLogEvent } from "@/lib/types";

type ConnectionState = "connecting" | "open" | "closed";

const SCRAPER_LOGS_PATH = "/api/v1/admin/scraper/runs/{taskId}/logs";

export function ScraperLogConsole({
  taskId,
  creatorId,
  title = "Live bot log",
  path = SCRAPER_LOGS_PATH,
  idleMessage = "Start a scrape to stream live activity.",
}: {
  taskId: string | null;
  creatorId?: string;
  title?: string;
  path?: string;
  idleMessage?: string;
}) {
  const [events, setEvents] = useState<ScraperLogEvent[]>([]);
  const [connection, setConnection] = useState<ConnectionState>(
    taskId ? "connecting" : "closed",
  );
  const scrollRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    if (!taskId) return;

    let closedByEffect = false;
    const resolvedPath = path
      .replace("{taskId}", taskId)
      .replace("{creatorId}", creatorId ?? "");
    const socket = new WebSocket(wsUrl(resolvedPath));
    socket.onopen = () => setConnection("open");
    socket.onmessage = (message) => {
      try {
        const event = JSON.parse(message.data as string) as ScraperLogEvent;
        setEvents((current) => [...current, event]);
      } catch {
        // ignore malformed frames
      }
    };
    socket.onclose = () => {
      if (!closedByEffect) setConnection("closed");
    };
    socket.onerror = () => socket.close();

    return () => {
      closedByEffect = true;
      socket.close();
    };
  }, [taskId, path, creatorId]);

  useEffect(() => {
    const node = scrollRef.current;
    if (node) node.scrollTop = node.scrollHeight;
  }, [events]);

  return (
    <section className="card" aria-labelledby="log-console-heading">
      <div className="card-header flex items-center justify-between gap-3">
        <div>
          <h2 id="log-console-heading" className="section-title">
            {title}
          </h2>
          <p className="mt-1 text-sm text-slate-500">
            {taskId
              ? `Streaming ${title.toLowerCase()} in real time.`
              : idleMessage}
          </p>
        </div>
        {taskId && (
          <div className="flex items-center gap-3">
            <button
              type="button"
              className="text-sm font-medium text-slate-500 hover:text-red-600 disabled:opacity-50"
              onClick={() => setEvents([])}
              disabled={events.length === 0}
              aria-label="Clear log"
            >
              Clear
            </button>
            <span
              className={`status ${
                connection === "open"
                  ? "status-success"
                  : connection === "connecting"
                    ? "status-active"
                    : "status-danger"
              }`}
            >
              {connection}
            </span>
          </div>
        )}
      </div>
      <div className="p-5 sm:p-6">
        <div ref={scrollRef} className="log-console" role="log" aria-live="polite">
          {events.length ? (
            events.map((event, index) => (
              <div
                key={`${event.ts}-${index}`}
                className={`log-line log-${event.level}`}
              >
                <span className="log-time">{formatTime(event.ts)}</span>
                {event.step && <span className="log-step">{event.step}</span>}
                <span className="log-message">{event.message}</span>
                {event.data && event.level !== "error" && <LogData data={event.data} />}
                {event.level === "error" && event.data && (
                  <pre className="mt-1 whitespace-pre-wrap break-all rounded bg-red-950/30 p-2 text-xs text-red-200">
                    {JSON.stringify(event.data, null, 2)}
                  </pre>
                )}
              </div>
            ))
          ) : (
            <p className="log-empty">
              {taskId ? "Waiting for events..." : "No active run."}
            </p>
          )}
        </div>
      </div>
    </section>
  );
}

function LogData({ data }: { data: Record<string, unknown> }) {
  const entries = Object.entries(data).filter(
    ([key]) => key !== "ts" && key !== "level" && key !== "message" && key !== "step" && key !== "terminal",
  );
  if (!entries.length) return null;
  return (
    <span className="log-data ml-2 flex flex-wrap gap-1">
      {entries.map(([key, value]) => (
        <span
          key={key}
          className="inline-flex items-center rounded bg-slate-100 px-1.5 py-0.5 text-xs text-slate-600"
          title={`${key}: ${String(value)}`}
        >
          {key}: {String(value).slice(0, 40)}
        </span>
      ))}
    </span>
  );
}

function formatTime(ts: string): string {
  const date = new Date(ts);
  return Number.isNaN(date.getTime())
    ? "--:--:--"
    : date.toLocaleTimeString([], { hour12: false });
}
