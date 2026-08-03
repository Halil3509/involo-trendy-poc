"use client";

import { FormEvent, useCallback, useEffect, useState } from "react";

import { ApiError, api } from "@/lib/api";
import { usePolling } from "@/lib/hooks";
import type { Job, ProfilingConfig, ProfilingEstimate } from "@/lib/types";
import { isRunActive, isValidCron } from "@/lib/validation";

const defaultConfig: ProfilingConfig = { enabled: false, schedule_cron: null };

export function ProfilingAdmin() {
  const [config, setConfig] = useState(defaultConfig);
  const [estimate, setEstimate] = useState<ProfilingEstimate | null>(null);
  const [run, setRun] = useState<Job | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [starting, setStarting] = useState(false);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const cronError =
    config.schedule_cron && !isValidCron(config.schedule_cron)
      ? "Enter a valid five-field cron expression."
      : "";

  const loadLatest = useCallback(async () => {
    try {
      setRun(await api.getLatestProfilingRun());
    } catch (caught) {
      if (caught instanceof ApiError && caught.status === 404) {
        setRun(null);
        return;
      }
      throw caught;
    }
  }, []);

  const loadPage = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const [savedConfig, nextEstimate] = await Promise.all([
        api.getProfilingConfig(),
        api.getProfilingEstimate(),
        loadLatest(),
      ]);
      setConfig(savedConfig);
      setEstimate(nextEstimate);
    } catch (caught) {
      setError(
        caught instanceof Error ? caught.message : "Unable to load profiling data.",
      );
    } finally {
      setLoading(false);
    }
  }, [loadLatest]);

  useEffect(() => {
    const timer = window.setTimeout(() => void loadPage(), 0);
    return () => window.clearTimeout(timer);
  }, [loadPage]);

  const pollRun = useCallback(async () => {
    const [nextEstimate] = await Promise.all([
      api.getProfilingEstimate(),
      loadLatest(),
    ]);
    setEstimate(nextEstimate);
  }, [loadLatest]);

  usePolling(pollRun, isRunActive(run), {
    onError: (err) => setError(err.message),
  });

  async function save(event: FormEvent) {
    event.preventDefault();
    if (cronError) return;
    setSaving(true);
    setError("");
    setMessage("");
    try {
      const saved = await api.updateProfilingConfig({
        enabled: config.enabled,
        schedule_cron: config.schedule_cron?.trim() || null,
      });
      setConfig(saved);
      setEstimate(await api.getProfilingEstimate());
      setMessage("Profiling schedule saved.");
    } catch (caught) {
      setError(
        caught instanceof Error ? caught.message : "Unable to save schedule.",
      );
    } finally {
      setSaving(false);
    }
  }

  async function startRun() {
    setStarting(true);
    setError("");
    setMessage("");
    try {
      const nextRun = await api.startProfilingRun();
      setRun(nextRun);
      setMessage("Profiling run started.");
    } catch (caught) {
      setError(
        caught instanceof Error ? caught.message : "Unable to start profiling.",
      );
    } finally {
      setStarting(false);
    }
  }

  if (loading) {
    return (
      <div className="page-center min-h-[60vh]" role="status">
        <span className="spinner" aria-hidden="true" />
        <span>Loading profiling controls...</span>
      </div>
    );
  }

  const active = isRunActive(run);

  return (
    <main className="page-container">
      <div className="mb-8 flex flex-col justify-between gap-4 sm:flex-row sm:items-end">
        <div>
          <p className="eyebrow">Admin workspace</p>
          <h1 className="page-title mt-2">Instagram profiling</h1>
          <p className="mt-2 max-w-2xl text-sm leading-6 text-slate-500">
            Schedule profile refreshes, estimate capacity, and monitor the latest
            batch.
          </p>
        </div>
        <button
          type="button"
          className="button button-primary"
          disabled={starting || active}
          onClick={() => void startRun()}
        >
          {(starting || active) && <span className="spinner spinner-light" />}
          {starting ? "Starting..." : active ? "Run in progress" : "Run profiling now"}
        </button>
      </div>

      {error && (
        <div className="alert alert-error mb-5" role="alert">
          {error}
        </div>
      )}
      {message && (
        <div className="alert alert-success mb-5" role="status">
          {message}
        </div>
      )}

      <div className="grid gap-6 lg:grid-cols-[0.9fr_1.1fr] lg:items-start">
        <section className="card" aria-labelledby="profiling-config-heading">
          <div className="card-header">
            <h2 id="profiling-config-heading" className="section-title">
              Schedule
            </h2>
            <p className="mt-1 text-sm text-slate-500">
              Times are interpreted in UTC.
            </p>
          </div>
          <form className="space-y-5 p-5 sm:p-6" onSubmit={save} noValidate>
            <label className="toggle-row">
              <span>
                <span className="block text-sm font-medium text-slate-800">
                  Automatic profiling
                </span>
                <span className="mt-1 block text-sm text-slate-500">
                  Refresh every connected user on schedule.
                </span>
              </span>
              <input
                className="toggle"
                type="checkbox"
                checked={config.enabled}
                onChange={(event) =>
                  setConfig((current) => ({
                    ...current,
                    enabled: event.target.checked,
                  }))
                }
              />
            </label>
            <div>
              <label className="label" htmlFor="profiling-cron">
                Schedule (cron)
              </label>
              <input
                id="profiling-cron"
                className="input"
                value={config.schedule_cron ?? ""}
                placeholder="e.g. 0 3 * * *"
                aria-invalid={Boolean(cronError)}
                aria-describedby="profiling-cron-help"
                onChange={(event) =>
                  setConfig((current) => ({
                    ...current,
                    schedule_cron: event.target.value,
                  }))
                }
              />
              <p
                id="profiling-cron-help"
                className={cronError ? "field-error" : "field-hint"}
              >
                {cronError || "Five fields in UTC. Empty removes the schedule."}
              </p>
            </div>
            <button
              className="button button-primary"
              disabled={saving || Boolean(cronError)}
            >
              {saving && <span className="spinner spinner-light" />}
              {saving ? "Saving..." : "Save schedule"}
            </button>
          </form>
        </section>

        <div className="space-y-6">
          <EstimatePanel estimate={estimate} />
          <LatestRunPanel run={run} />
        </div>
      </div>
    </main>
  );
}

function EstimatePanel({ estimate }: { estimate: ProfilingEstimate | null }) {
  return (
    <section className="card" aria-labelledby="estimate-heading">
      <div className="card-header">
        <h2 id="estimate-heading" className="section-title">
          Capacity estimate
        </h2>
      </div>
      {estimate ? (
        <div className="grid grid-cols-2 gap-3 p-5 sm:grid-cols-3 sm:p-6">
          <Metric label="Connected users" value={String(estimate.connected_users)} />
          <Metric
            label="Average per user"
            value={formatDuration(estimate.average_seconds_per_user)}
          />
          <Metric
            label="Total duration"
            value={formatDuration(estimate.estimated_duration_seconds)}
          />
          <Metric label="Estimated start" value={formatDate(estimate.estimated_start_at)} />
          <Metric
            label="Estimated finish"
            value={formatDate(estimate.estimated_finish_at)}
          />
        </div>
      ) : (
        <p className="p-6 text-sm text-slate-500">No estimate is available.</p>
      )}
    </section>
  );
}

function LatestRunPanel({ run }: { run: Job | null }) {
  return (
    <section className="card" aria-labelledby="profiling-run-heading">
      <div className="card-header flex items-start justify-between gap-3">
        <div>
          <h2 id="profiling-run-heading" className="section-title">
            Latest run
          </h2>
          <p className="mt-1 text-sm text-slate-500">
            {run?.id ? `Job ${run.id.slice(0, 8)}` : "No profiling run yet."}
          </p>
        </div>
        {run && <StatusBadge status={run.state} />}
      </div>
      {run && (
        <div className="p-5 sm:p-6">
          <div className="grid grid-cols-2 gap-3">
            {Object.entries(run.counters ?? {}).map(([label, value]) => (
              <Metric key={label} label={label.replaceAll("_", " ")} value={String(value)} />
            ))}
          </div>
          {run.error && (
            <div className="alert alert-error mt-4" role="alert">
              {run.error}
            </div>
          )}
          <dl className="mt-5 space-y-2 border-t border-slate-100 pt-5 text-sm">
            <TimeRow label="Started" value={run.started_at ?? run.created_at} />
            <TimeRow label="Finished" value={run.finished_at} />
          </dl>
        </div>
      )}
    </section>
  );
}

function Metric({ label, value }: { label: string; value: string }) {
  return (
    <dl className="metric">
      <dt className="capitalize">{label}</dt>
      <dd className="text-base! tracking-normal!">{value}</dd>
    </dl>
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

function TimeRow({ label, value }: { label: string; value?: string | null }) {
  return (
    <div className="flex justify-between gap-4">
      <dt className="text-slate-500">{label}</dt>
      <dd className="text-right font-medium text-slate-700">{formatDate(value)}</dd>
    </div>
  );
}

function formatDate(value?: string | null): string {
  return value ? new Date(value).toLocaleString() : "—";
}

function formatDuration(seconds: number): string {
  if (seconds < 60) return `${Math.round(seconds)} sec`;
  const minutes = Math.round(seconds / 60);
  if (minutes < 60) return `${minutes} min`;
  const hours = Math.floor(minutes / 60);
  const remainder = minutes % 60;
  return remainder ? `${hours}h ${remainder}m` : `${hours}h`;
}
