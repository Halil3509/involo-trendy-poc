"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";

import { AdminObservability } from "@/components/admin-observability";
import { api } from "@/lib/api";
import { usePolling } from "@/lib/hooks";
import type { AdminOverview, Job } from "@/lib/types";
import { isRunActive } from "@/lib/validation";

const ATTENTION_STATES = new Set(["failed", "needs_intervention"]);

export function AdminDashboard() {
  const [overview, setOverview] = useState<AdminOverview | null>(null);
  const [jobs, setJobs] = useState<Job[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  const load = useCallback(async () => {
    const [nextOverview, nextJobs] = await Promise.all([
      api.getAdminOverview(),
      api.getAdminJobs({ limit: 20 }),
    ]);
    setOverview(nextOverview);
    setJobs(nextJobs);
  }, []);

  useEffect(() => {
    let cancelled = false;
    const run = async () => {
      try {
        await load();
      } catch (caught) {
        if (!cancelled) {
          setError(
            caught instanceof Error ? caught.message : "Unable to load overview.",
          );
        }
      } finally {
        if (!cancelled) setLoading(false);
      }
    };
    void run();
    return () => {
      cancelled = true;
    };
  }, [load]);

  usePolling(
    load,
    jobs.some((job) => isRunActive(job)),
    { interval: 4000, onError: (err) => setError(err.message) },
  );

  if (loading) {
    return (
      <div className="page-center min-h-[60vh]" role="status">
        <span className="spinner" aria-hidden="true" />
        <span>Loading admin overview...</span>
      </div>
    );
  }

  const attentionJobs = jobs.filter((job) =>
    ATTENTION_STATES.has(job.state.toLowerCase()),
  );
  const needsReauth = overview?.needs_reauth ?? 0;

  return (
    <main className="page-container">
      <div className="mb-8">
        <p className="eyebrow">Admin workspace</p>
        <h1 className="page-title mt-2">Operations overview</h1>
        <p className="mt-2 max-w-2xl text-sm leading-6 text-slate-500">
          Content pipeline health, connected creators, and recent background jobs
          at a glance.
        </p>
      </div>

      {error && (
        <div className="alert alert-error mb-5" role="alert">
          {error}
        </div>
      )}

      {overview && (
        <>
          <section aria-label="Key metrics" className="stat-grid">
            <StatTile label="Users" value={overview.total_users} hint={`${overview.admin_users} admin`} />
            <StatTile
              label="Connected Instagram"
              value={overview.connected_instagram}
              hint={needsReauth ? `${needsReauth} need reauth` : "all healthy"}
              tone={needsReauth ? "warn" : "default"}
            />
            <StatTile label="Trend content" value={overview.trend_content_total} />
            <StatTile label="Profiles ready" value={overview.user_profiles_ready} />
            <StatTile label="Recommendations" value={overview.recommendation_batches} />
          </section>

          <div className="mt-6 grid gap-6 lg:grid-cols-[1.3fr_0.7fr]">
            <section className="card" aria-labelledby="pipeline-overview">
              <div className="card-header">
                <h2 id="pipeline-overview" className="section-title">
                  Content pipeline
                </h2>
                <p className="mt-1 text-sm text-slate-500">
                  Documents by processing stage.
                </p>
              </div>
              <div className="grid grid-cols-2 gap-3 p-5 sm:grid-cols-3 sm:p-6">
                <Metric label="Discovered" value={overview.pipeline.discovered} />
                <Metric label="Enriched" value={overview.pipeline.enriched} />
                <Metric label="Stored" value={overview.pipeline.stored} />
                <Metric label="Embedded" value={overview.pipeline.embedded} />
                <Metric label="User content" value={overview.user_content_total} />
              </div>
            </section>

            <AttentionPanel
              jobs={attentionJobs}
              needsReauth={needsReauth}
              pipelineFailures={
                overview.pipeline.failed + overview.pipeline.needs_intervention
              }
            />
          </div>
        </>
      )}

      <section className="card mt-6" aria-labelledby="recent-jobs">
        <div className="card-header flex items-center justify-between gap-3">
          <h2 id="recent-jobs" className="section-title">
            Recent jobs
          </h2>
          <div className="flex gap-2 text-sm">
            <Link href="/admin/scraper" className="nav-link">
              Scraper
            </Link>
            <Link href="/admin/profiling" className="nav-link">
              Profiling
            </Link>
          </div>
        </div>
        <JobsTable jobs={jobs} />
      </section>
      <AdminObservability />
    </main>
  );
}

function AttentionPanel({
  jobs,
  needsReauth,
  pipelineFailures,
}: {
  jobs: Job[];
  needsReauth: number;
  pipelineFailures: number;
}) {
  const clear = jobs.length === 0 && needsReauth === 0 && pipelineFailures === 0;
  return (
    <section className="card self-start" aria-labelledby="attention">
      <div className="card-header">
        <h2 id="attention" className="section-title">
          Attention needed
        </h2>
      </div>
      <div className="space-y-3 p-5 sm:p-6">
        {clear ? (
          <p className="flex items-center gap-2 text-sm text-emerald-700">
            <span className="h-2 w-2 rounded-full bg-emerald-500" />
            Everything is healthy.
          </p>
        ) : (
          <>
            {needsReauth > 0 && (
              <div className="alert alert-error">
                {needsReauth} Instagram connection(s) need re-authentication.
              </div>
            )}
            {pipelineFailures > 0 && (
              <div className="alert alert-error">
                {pipelineFailures} content item(s) failed or need intervention.
              </div>
            )}
            {jobs.map((job, index) => (
              <div
                key={job.id || `${job.kind}-${index}`}
                className="rounded-lg bg-red-50 p-3 text-sm text-red-700"
              >
                <div className="flex items-center justify-between gap-2">
                  <span className="font-semibold capitalize">
                    {job.kind.replaceAll("_", " ")}
                  </span>
                  <StatusBadge status={job.state} />
                </div>
                {job.error && <p className="mt-1 break-words">{job.error}</p>}
              </div>
            ))}
          </>
        )}
      </div>
    </section>
  );
}

function JobsTable({ jobs }: { jobs: Job[] }) {
  if (!jobs.length) {
    return (
      <p className="p-6 text-sm text-slate-500">No jobs have been run yet.</p>
    );
  }
  return (
    <div className="overflow-x-auto">
      <table className="data-table">
        <thead>
          <tr>
            <th>Kind</th>
            <th>State</th>
            <th>Counters</th>
            <th>Created</th>
          </tr>
        </thead>
        <tbody>
          {jobs.map((job, index) => (
            <tr key={job.id || `${job.kind}-${index}`}>
              <td className="font-medium capitalize text-slate-800">
                {job.kind.replaceAll("_", " ")}
              </td>
              <td>
                <StatusBadge status={job.state} />
              </td>
              <td className="text-slate-600">{summarizeCounters(job.counters)}</td>
              <td className="text-slate-500">
                {job.created_at
                  ? new Date(job.created_at).toLocaleString()
                  : "—"}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function summarizeCounters(counters: Record<string, number>): string {
  const entries = Object.entries(counters ?? {});
  if (!entries.length) return "—";
  return entries.map(([key, value]) => `${key.replaceAll("_", " ")}: ${value}`).join(", ");
}

function StatTile({
  label,
  value,
  hint,
  tone = "default",
}: {
  label: string;
  value: number;
  hint?: string;
  tone?: "default" | "warn";
}) {
  return (
    <div className="stat-tile">
      <p className="stat-tile-label">{label}</p>
      <p className="stat-tile-value">{value}</p>
      {hint && (
        <p className={tone === "warn" ? "stat-tile-hint-warn" : "stat-tile-hint"}>
          {hint}
        </p>
      )}
    </div>
  );
}

function Metric({ label, value }: { label: string; value: number }) {
  return (
    <div className="metric">
      <dt className="capitalize">{label}</dt>
      <dd>{value}</dd>
    </div>
  );
}

function StatusBadge({ status }: { status: string }) {
  const normalized = status.toLowerCase();
  const tone =
    normalized === "succeeded"
      ? "status-success"
      : normalized === "failed" ||
          normalized === "needs_intervention" ||
          normalized === "cancelled"
        ? "status-danger"
        : "status-active";
  return <span className={`status ${tone}`}>{status.replaceAll("_", " ")}</span>;
}
