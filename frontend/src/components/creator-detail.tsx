"use client";

import { useCallback, useEffect, useState } from "react";

import { ApiError, api } from "@/lib/api";
import { ScraperLogConsole } from "@/components/scraper-log-console";
import type {
  CreatorContentItem,
  FollowerHistory,
  FollowerHistoryRange,
  Job,
  TrackedCreatorDetail,
} from "@/lib/types";

const RANGES: { value: FollowerHistoryRange; label: string }[] = [
  { value: "week", label: "Week" },
  { value: "month", label: "Month" },
  { value: "year", label: "Year" },
];

function formatCount(value: number): string {
  if (value >= 1_000_000) return `${(value / 1_000_000).toFixed(1)}M`;
  if (value >= 1_000) return `${(value / 1_000).toFixed(1)}K`;
  return String(value);
}

function FollowerChart({ history }: { history: FollowerHistory }) {
  if (history.points.length === 0) {
    return (
      <p className="p-6 text-sm text-slate-500">
        No snapshots in this range yet. Daily snapshots start after the first
        tracking run.
      </p>
    );
  }
  const values = history.points.map((point) => point.follower_count);
  const min = Math.min(...values);
  const max = Math.max(...values);
  const spread = max - min || 1;
  const width = 600;
  const height = 180;
  const coords = history.points.map((point, index) => ({
    x: history.points.length === 1 ? width / 2 : (index / (history.points.length - 1)) * width,
    y: height - ((point.follower_count - min) / spread) * (height - 20) - 10,
  }));
  const path = coords
    .map((coord, index) => `${index === 0 ? "M" : "L"}${coord.x.toFixed(1)},${coord.y.toFixed(1)}`)
    .join(" ");
  return (
    <div className="p-5 sm:p-6">
      <div className="flex items-baseline justify-between">
        <p className="text-sm text-slate-500">
          {formatCount(history.points[0].follower_count)} →{" "}
          <span className="font-semibold text-slate-900">
            {formatCount(history.points[history.points.length - 1].follower_count)}
          </span>
        </p>
        <p
          className={`text-sm font-semibold ${
            history.delta >= 0 ? "text-emerald-700" : "text-red-600"
          }`}
        >
          {history.delta >= 0 ? "+" : ""}
          {formatCount(history.delta)}
        </p>
      </div>
      <svg
        viewBox={`0 0 ${width} ${height}`}
        className="mt-4 h-44 w-full"
        role="img"
        aria-label="Follower count over time"
        preserveAspectRatio="none"
      >
        <path d={path} fill="none" stroke="#6366f1" strokeWidth={2.5} />
        {coords.map((coord, index) => (
          <circle key={index} cx={coord.x} cy={coord.y} r={3} fill="#6366f1" />
        ))}
      </svg>
    </div>
  );
}

function ContentCard({ item }: { item: CreatorContentItem }) {
  return (
    <li className="card overflow-hidden">
      <div className="flex h-32 items-center justify-center bg-slate-100 text-xs font-semibold uppercase tracking-wide text-slate-400">
        {item.media_type}
      </div>
      <div className="p-4">
        <div className="flex items-center justify-between gap-2">
          <span className="rounded-full bg-indigo-50 px-2.5 py-1 text-xs font-semibold text-indigo-700">
            {item.viral_score.toFixed(0)} viral
          </span>
          {item.is_new && (
            <span className="rounded-full bg-emerald-50 px-2.5 py-1 text-xs font-semibold text-emerald-700">
              New
            </span>
          )}
        </div>
        <p className="mt-3 line-clamp-2 text-sm text-slate-700">
          {item.caption_text || "No caption"}
        </p>
        <dl className="mt-3 flex items-center gap-4 text-xs text-slate-500">
          <div>
            <dt className="sr-only">Views</dt>
            <dd>{formatCount(item.view_count)} views</dd>
          </div>
          <div>
            <dt className="sr-only">Likes</dt>
            <dd>{formatCount(item.like_count)} likes</dd>
          </div>
          <div>
            <dt className="sr-only">Comments</dt>
            <dd>{formatCount(item.comment_count)} comments</dd>
          </div>
        </dl>
        {item.permalink && (
          <a
            href={item.permalink}
            target="_blank"
            rel="noreferrer"
            className="mt-3 inline-block text-xs font-semibold text-indigo-700 hover:text-indigo-500"
          >
            View on Instagram
          </a>
        )}
      </div>
    </li>
  );
}

export function CreatorDetail({ creatorId }: { creatorId: string }) {
  const [creator, setCreator] = useState<TrackedCreatorDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [range, setRange] = useState<FollowerHistoryRange>("month");
  const [history, setHistory] = useState<FollowerHistory | null>(null);
  const [historyError, setHistoryError] = useState<string | null>(null);
  const [content, setContent] = useState<CreatorContentItem[]>([]);
  const [contentError, setContentError] = useState<string | null>(null);
  const [analyzing, setAnalyzing] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const [activeJob, setActiveJob] = useState<Job | null>(null);
  const [logsOpen, setLogsOpen] = useState(false);

  useEffect(() => {
    let cancelled = false;
    api
      .getTrackedCreator(creatorId)
      .then((detail) => {
        if (cancelled) return;
        setCreator(detail);
        setError(null);
        setLoading(false);
      })
      .catch((caught: unknown) => {
        if (cancelled) return;
        setError(
          caught instanceof ApiError ? caught.message : "Failed to load creator",
        );
        setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [creatorId]);

  const loadHistory = useCallback(async (selected: FollowerHistoryRange) => {
    try {
      const result = await api.getTrackedCreatorFollowers(creatorId, selected);
      setHistory(result);
      setHistoryError(null);
    } catch (caught) {
      setHistoryError(
        caught instanceof ApiError ? caught.message : "Failed to load followers",
      );
    }
  }, [creatorId]);

  const loadContent = useCallback(async () => {
    try {
      const response = await api.getTrackedCreatorContent(creatorId, "viral");
      setContent(response.items);
      setContentError(null);
    } catch (caught) {
      setContentError(
        caught instanceof ApiError ? caught.message : "Failed to load content",
      );
    }
  }, [creatorId]);

  useEffect(() => {
    const timer = window.setTimeout(() => void loadHistory(range), 0);
    return () => window.clearTimeout(timer);
  }, [loadHistory, range]);

  useEffect(() => {
    const timer = window.setTimeout(() => void loadContent(), 0);
    return () => window.clearTimeout(timer);
  }, [loadContent]);

  async function handleAnalyze() {
    setAnalyzing(true);
    setNotice(null);
    setActiveJob(null);
    setLogsOpen(false);
    try {
      const job = await api.analyzeTrackedCreator(creatorId);
      setActiveJob(job);
      setLogsOpen(true);
      setNotice("Analysis queued. Live logs are streaming below.");
    } catch (caught) {
      setNotice(
        caught instanceof ApiError ? caught.message : "Failed to queue analysis",
      );
    } finally {
      setAnalyzing(false);
    }
  }

  if (loading) {
    return (
      <div className="p-6" role="status">
        Loading creator…
      </div>
    );
  }
  if (error || !creator) {
    return (
      <div className="alert alert-error" role="alert">
        {error ?? "Creator not found"}
      </div>
    );
  }

  const pillars = (
    (creator.structured_profile?.pillars as { name?: string }[] | undefined) ?? []
  ).slice(0, 4);

  return (
    <div className="space-y-6">
      {creator.status === "needs_intervention" && (
        <div className="alert alert-error" role="alert">
          Instagram requires verification for this profile. Daily tracking is
          paused until the scraper session is refreshed.
        </div>
      )}
      {notice && (
        <div className="alert alert-success" role="status">
          {notice}
        </div>
      )}

      {activeJob && (
        <section className="card" aria-labelledby="analysis-logs-heading">
          <button
            type="button"
            onClick={() => setLogsOpen((open) => !open)}
            aria-expanded={logsOpen}
            aria-controls="analysis-logs-panel"
            className="card-header flex w-full items-center justify-between text-left"
          >
            <div>
              <h2 id="analysis-logs-heading" className="section-title">
                Live analysis logs
              </h2>
              <p className="mt-1 text-sm text-slate-500">
                {logsOpen
                  ? "Streaming real-time analysis activity."
                  : "Click to view real-time analysis activity."}
              </p>
            </div>
            <span aria-hidden="true" className="text-lg text-slate-500">
              {logsOpen ? "▾" : "▸"}
            </span>
          </button>
          {logsOpen && (
            <div id="analysis-logs-panel">
              <ScraperLogConsole
                key={activeJob.id}
                taskId={activeJob.id}
                creatorId={creatorId}
                title="Creator analysis log"
                path="/api/v1/creators/{creatorId}/analyze/{taskId}/logs"
                idleMessage="Start analysis to stream live activity."
              />
            </div>
          )}
        </section>
      )}

      <section className="card" aria-labelledby="creator-heading">
        <div className="flex flex-wrap items-center gap-5 p-5 sm:p-6">
          <div className="flex h-16 w-16 shrink-0 items-center justify-center rounded-full bg-indigo-100 text-xl font-semibold uppercase text-indigo-700">
            {creator.username.slice(0, 2)}
          </div>
          <div className="min-w-0 flex-1">
            <h1 id="creator-heading" className="page-title text-2xl">
              @{creator.username}
            </h1>
            {creator.display_name && (
              <p className="text-sm text-slate-500">{creator.display_name}</p>
            )}
            {creator.bio && (
              <p className="mt-2 max-w-2xl text-sm leading-6 text-slate-600">
                {creator.bio}
              </p>
            )}
          </div>
          <button
            type="button"
            className="button"
            disabled={analyzing}
            onClick={() => void handleAnalyze()}
          >
            {analyzing ? "Queueing…" : "Analyze now"}
          </button>
        </div>
        <div className="stat-grid p-5 pt-0 sm:grid-cols-4 sm:p-6 sm:pt-0">
          <div className="stat-tile">
            <p className="stat-tile-label">Followers</p>
            <p className="stat-tile-value">{formatCount(creator.follower_count)}</p>
          </div>
          <div className="stat-tile">
            <p className="stat-tile-label">Following</p>
            <p className="stat-tile-value">{formatCount(creator.following_count)}</p>
          </div>
          <div className="stat-tile">
            <p className="stat-tile-label">Trend score</p>
            <p className="stat-tile-value">{creator.trend_score.toFixed(1)}</p>
          </div>
          <div className="stat-tile">
            <p className="stat-tile-label">Avg. viral score</p>
            <p className="stat-tile-value">
              {creator.average_viral_score != null
                ? creator.average_viral_score.toFixed(1)
                : "—"}
            </p>
          </div>
        </div>
      </section>

      {(creator.ai_summary || pillars.length > 0) && (
        <section className="card" aria-labelledby="ai-profile-heading">
          <div className="card-header">
            <h2 id="ai-profile-heading" className="section-title">
              AI creator profile
            </h2>
          </div>
          <div className="p-5 sm:p-6">
            {creator.ai_summary && (
              <p className="text-sm leading-6 text-slate-700">
                {creator.ai_summary}
              </p>
            )}
            {pillars.length > 0 && (
              <ul className="mt-4 flex flex-wrap gap-2">
                {pillars.map((pillar, index) => (
                  <li key={index} className="chip">
                    {pillar.name}
                  </li>
                ))}
              </ul>
            )}
          </div>
        </section>
      )}

      <section className="card" aria-labelledby="followers-heading">
        <div className="card-header flex items-center justify-between">
          <h2 id="followers-heading" className="section-title">
            Follower history
          </h2>
          <div className="flex gap-1" role="group" aria-label="History range">
            {RANGES.map((option) => (
              <button
                key={option.value}
                type="button"
                aria-pressed={range === option.value}
                className={`rounded-full px-3 py-1 text-xs font-semibold ${
                  range === option.value
                    ? "bg-indigo-600 text-white"
                    : "bg-slate-100 text-slate-600 hover:bg-slate-200"
                }`}
                onClick={() => setRange(option.value)}
              >
                {option.label}
              </button>
            ))}
          </div>
        </div>
        {historyError ? (
          <div className="alert alert-error m-5" role="alert">
            {historyError}
          </div>
        ) : history ? (
          <FollowerChart history={history} />
        ) : (
          <div className="p-6" role="status">
            Loading follower history…
          </div>
        )}
      </section>

      <section aria-labelledby="content-heading">
        <div className="card-header">
          <h2 id="content-heading" className="section-title">
            Top content
          </h2>
        </div>
        {contentError ? (
          <div className="alert alert-error" role="alert">
            {contentError}
          </div>
        ) : content.length === 0 ? (
          <div className="card p-6 text-sm text-slate-500">
            No content analyzed yet. Run Analyze now to fetch recent posts.
          </div>
        ) : (
          <ul className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
            {content.map((item) => (
              <ContentCard key={item.shortcode} item={item} />
            ))}
          </ul>
        )}
      </section>
    </div>
  );
}
