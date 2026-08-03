"use client";

import {
  FormEvent,
  KeyboardEvent,
  useCallback,
  useEffect,
  useState,
} from "react";

import { ApiError, api } from "@/lib/api";
import { ScraperLogConsole } from "@/components/scraper-log-console";
import { TrendContentTable } from "@/components/trend-content-table";
import { usePolling } from "@/lib/hooks";
import type { Job, PipelineStats, ScraperConfig } from "@/lib/types";
import { addKeyword, isRunActive, isValidCron, runErrors } from "@/lib/validation";

const defaultConfig: ScraperConfig = {
  keywords: [],
  reels_per_keyword: 10,
  headless: true,
  viral_threshold: 0,
  schedule_cron: "",
  schedule_pipeline: false,
};

type PipelineStage = "enrich" | "embed";

const STAGE_LABELS: Record<PipelineStage, string> = {
  enrich: "Enrich",
  embed: "Embed",
};

export function ScraperAdmin() {
  const [config, setConfig] = useState(defaultConfig);
  const [keyword, setKeyword] = useState("");
  const [latestRun, setLatestRun] = useState<Job | null>(null);
  const [pipelineRun, setPipelineRun] = useState<Job | null>(null);
  const [recentJobs, setRecentJobs] = useState<Job[]>([]);
  const [selectedJobId, setSelectedJobId] = useState<string | null>(null);
  const [stats, setStats] = useState<PipelineStats | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [starting, setStarting] = useState(false);
  const [pipelineBusy, setPipelineBusy] = useState<PipelineStage | null>(null);
  const [fullPipelineBusy, setFullPipelineBusy] = useState(false);
  const [stopping, setStopping] = useState<string | null>(null);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");

  const loadLatest = useCallback(async () => {
    try {
      setLatestRun(await api.getLatestScraperRun());
    } catch (caught) {
      if (!(caught instanceof ApiError) || caught.status !== 404) throw caught;
      setLatestRun(null);
    }
  }, []);

  const loadPipeline = useCallback(async () => {
    const [nextStats, nextRun] = await Promise.all([
      api.getPipelineStats(),
      api
        .getLatestPipelineRun()
        .catch((caught) => {
          if (caught instanceof ApiError && caught.status === 404) return null;
          throw caught;
        }),
    ]);
    setStats(nextStats);
    setPipelineRun(nextRun);
  }, []);

  const loadJobs = useCallback(async () => {
    try {
      const jobs = await api.getAdminJobs({ limit: 20 });
      setRecentJobs(jobs);
    } catch (caught) {
      if (!(caught instanceof ApiError) || caught.status !== 404) throw caught;
    }
  }, []);

  const loadPage = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const [savedConfig] = await Promise.all([
        api.getScraperConfig(),
        loadLatest(),
        loadPipeline(),
        loadJobs(),
      ]);
      setConfig({ ...defaultConfig, ...savedConfig });
    } catch (caught) {
      setError(
        caught instanceof Error ? caught.message : "Unable to load scraper data.",
      );
    } finally {
      setLoading(false);
    }
  }, [loadLatest, loadPipeline, loadJobs]);

  useEffect(() => {
    const timer = window.setTimeout(() => void loadPage(), 0);
    return () => window.clearTimeout(timer);
  }, [loadPage]);

  const pollActiveJobs = useCallback(async () => {
    await Promise.all([loadLatest(), loadPipeline(), loadJobs()]);
  }, [loadLatest, loadPipeline, loadJobs]);

  usePolling(pollActiveJobs, isRunActive(latestRun) || isRunActive(pipelineRun), {
    onError: (err) => setError(err.message),
  });

  function commitKeyword() {
    const next = addKeyword(config.keywords, keyword);
    if (next !== config.keywords) {
      setConfig((current) => ({ ...current, keywords: next }));
    }
    setKeyword("");
  }

  function handleKeywordKeyDown(event: KeyboardEvent<HTMLInputElement>) {
    if (event.key === "Enter" || event.key === ",") {
      event.preventDefault();
      commitKeyword();
    }
  }

  async function saveConfig(event: FormEvent) {
    event.preventDefault();
    if (!config.keywords.length) {
      setError("Add at least one keyword before saving.");
      return;
    }
    if (config.schedule_cron && !isValidCron(config.schedule_cron)) {
      setError("Schedule must be a 5-field cron expression (or empty).");
      return;
    }
    setSaving(true);
    setError("");
    setMessage("");
    try {
      const saved = await api.updateScraperConfig({
        ...config,
        schedule_cron: config.schedule_cron?.trim() || null,
      });
      setConfig({ ...defaultConfig, ...saved });
      setMessage("Scraper configuration saved.");
    } catch (caught) {
      setError(
        caught instanceof Error ? caught.message : "Unable to save configuration.",
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
      setLatestRun(await api.startScraperRun());
      setMessage("Scraper run started.");
    } catch (caught) {
      setError(
        caught instanceof Error ? caught.message : "Unable to start scraper run.",
      );
    } finally {
      setStarting(false);
    }
  }

  async function startPipeline(stage: PipelineStage) {
    setPipelineBusy(stage);
    setError("");
    setMessage("");
    try {
      setPipelineRun(await api.startPipeline(stage));
      setMessage(`${STAGE_LABELS[stage]} job started.`);
    } catch (caught) {
      setError(
        caught instanceof Error
          ? caught.message
          : `Unable to start ${STAGE_LABELS[stage].toLowerCase()} job.`,
      );
    } finally {
      setPipelineBusy(null);
    }
  }

  async function startFullPipeline() {
    setFullPipelineBusy(true);
    setError("");
    setMessage("");
    try {
      setPipelineRun(await api.startFullPipeline());
      setMessage("Full pipeline run started.");
    } catch (caught) {
      setError(
        caught instanceof Error
          ? caught.message
          : "Unable to start full pipeline run.",
      );
    } finally {
      setFullPipelineBusy(false);
    }
  }

  async function stopJob(id: string) {
    setStopping(id);
    setError("");
    setMessage("");
    try {
      await api.stopJob(id);
      setMessage(`Stop requested for job ${id.slice(0, 8)}.`);
      await Promise.all([loadLatest(), loadPipeline(), loadJobs()]);
    } catch (caught) {
      setError(
        caught instanceof Error ? caught.message : "Unable to stop job.",
      );
    } finally {
      setStopping(null);
    }
  }

  if (loading) {
    return (
      <div className="page-center min-h-[60vh]" role="status">
        <span className="spinner" />
        <span>Loading scraper configuration...</span>
      </div>
    );
  }

  const active = isRunActive(latestRun);
  const pipelineActive = isRunActive(pipelineRun);
  const selectedJob = recentJobs.find((job) => job.id === selectedJobId) ?? null;

  return (
    <main className="page-container">
      <div className="mb-8">
        <p className="eyebrow">Admin workspace</p>
        <div className="mt-2 flex flex-col justify-between gap-4 sm:flex-row sm:items-end">
          <div>
            <h1 className="page-title">Scraper &amp; pipeline control</h1>
            <p className="mt-2 max-w-2xl text-sm leading-6 text-slate-500">
              Configure discovery, scoring thresholds and scheduling, then run the
              enrichment and embedding pipeline.
            </p>
          </div>
          <div className="flex gap-2">
            <button
              type="button"
              className="button button-primary"
              disabled={starting || active}
              onClick={() => void startRun()}
            >
              {(starting || active) && <span className="spinner spinner-light" />}
              {starting ? "Starting..." : active ? "Run in progress" : "Start scrape"}
            </button>
            {active && latestRun?.id && (
              <button
                type="button"
                className="button button-secondary"
                disabled={stopping === latestRun.id}
                onClick={() => void stopJob(latestRun.id)}
              >
                {stopping === latestRun.id && <span className="spinner" />}
                Stop
              </button>
            )}
          </div>
        </div>
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

      <div className="grid gap-6 lg:grid-cols-[1.1fr_0.9fr] lg:items-start">
        <section className="card" aria-labelledby="configuration-heading">
          <div className="card-header">
            <h2 id="configuration-heading" className="section-title">
              Configuration
            </h2>
            <p className="mt-1 text-sm text-slate-500">
              Changes apply to the next scraper and pipeline run.
            </p>
          </div>
          <form className="space-y-7 p-5 sm:p-6" onSubmit={saveConfig}>
            <div>
              <label className="label" htmlFor="keyword">
                Discovery keywords
              </label>
              <div className="flex gap-2">
                <input
                  id="keyword"
                  className="input"
                  value={keyword}
                  placeholder="e.g. sustainable fashion"
                  onChange={(event) => setKeyword(event.target.value)}
                  onKeyDown={handleKeywordKeyDown}
                />
                <button
                  type="button"
                  className="button button-secondary"
                  onClick={commitKeyword}
                >
                  Add
                </button>
              </div>
              <p className="field-hint">Press Enter or comma to add a keyword.</p>
              <div className="mt-3 flex flex-wrap gap-2" aria-live="polite">
                {config.keywords.length ? (
                  config.keywords.map((item) => (
                    <span className="chip" key={item}>
                      {item}
                      <button
                        type="button"
                        aria-label={`Remove ${item}`}
                        onClick={() =>
                          setConfig((current) => ({
                            ...current,
                            keywords: current.keywords.filter(
                              (value) => value !== item,
                            ),
                          }))
                        }
                      >
                        ×
                      </button>
                    </span>
                  ))
                ) : (
                  <p className="text-sm text-slate-400">No keywords added yet.</p>
                )}
              </div>
            </div>

            <div className="grid gap-5 sm:grid-cols-2">
              <div>
                <label className="label" htmlFor="reels-limit">
                  Media per keyword
                </label>
                <input
                  id="reels-limit"
                  className="input"
                  type="number"
                  min={1}
                  max={500}
                  required
                  value={config.reels_per_keyword}
                  onChange={(event) =>
                    setConfig((current) => ({
                      ...current,
                      reels_per_keyword: Number(event.target.value),
                    }))
                  }
                />
                <p className="field-hint">
                  The Meta adapter uses the top_media Graph API edge, so results
                  are usually images and carousels rather than Reels.
                </p>
              </div>
              <div>
                <label className="label" htmlFor="viral-threshold">
                  Viral threshold (0-100)
                </label>
                <input
                  id="viral-threshold"
                  className="input"
                  type="number"
                  min={0}
                  max={100}
                  step={0.5}
                  value={config.viral_threshold ?? 0}
                  onChange={(event) =>
                    setConfig((current) => ({
                      ...current,
                      viral_threshold: Number(event.target.value),
                    }))
                  }
                />
                <p className="field-hint">
                  Content below this score is stored but skips transcript.
                </p>
              </div>
            </div>

            <div>
              <label className="label" htmlFor="schedule-cron">
                Schedule (cron)
              </label>
              <input
                id="schedule-cron"
                className="input"
                placeholder="e.g. 0 5 * * *"
                value={config.schedule_cron ?? ""}
                onChange={(event) =>
                  setConfig((current) => ({
                    ...current,
                    schedule_cron: event.target.value,
                  }))
                }
              />
              <p className="field-hint">
                Five-field cron in UTC. Leave empty to disable.
              </p>
            </div>

            <label className="toggle-row">
              <span>
                <span className="block text-sm font-medium text-slate-800">
                  Headless browser
                </span>
                <span className="mt-1 block text-sm text-slate-500">
                  Run without opening a visible browser window.
                </span>
              </span>
              <input
                className="toggle"
                type="checkbox"
                checked={config.headless}
                onChange={(event) =>
                  setConfig((current) => ({
                    ...current,
                    headless: event.target.checked,
                  }))
                }
              />
            </label>
            <p className="field-hint -mt-3">
              A visible browser window only appears when the Playwright
              Instagram adapter is selected. The default Meta adapter uses the
              top_media Graph API edge and usually returns images and
              carousels rather than Reels.
            </p>

            <label className="toggle-row">
              <span>
                <span className="block text-sm font-medium text-slate-800">
                  Run full pipeline on schedule
                </span>
                <span className="mt-1 block text-sm text-slate-500">
                  After a scheduled scrape, also enrich and embed.
                </span>
              </span>
              <input
                className="toggle"
                type="checkbox"
                checked={config.schedule_pipeline ?? false}
                onChange={(event) =>
                  setConfig((current) => ({
                    ...current,
                    schedule_pipeline: event.target.checked,
                  }))
                }
              />
            </label>

            <button className="button button-primary" disabled={saving}>
              {saving && <span className="spinner spinner-light" />}
              {saving ? "Saving..." : "Save configuration"}
            </button>
          </form>
        </section>

        <div className="space-y-6">
          <PipelinePanel
            stats={stats}
            run={pipelineRun}
            busy={pipelineBusy}
            active={pipelineActive}
            fullBusy={fullPipelineBusy}
            stopping={stopping}
            onRun={(stage) => void startPipeline(stage)}
            onRunFull={() => void startFullPipeline()}
            onStop={(id) => void stopJob(id)}
          />
          <ScraperLogConsole
            key={pipelineRun?.id ?? "none"}
            taskId={pipelineRun?.id ?? null}
            title="Live pipeline log"
            path="/api/v1/admin/pipeline/runs/{taskId}/logs"
            idleMessage="Start the pipeline to stream live activity."
          />
          <RunPanel run={latestRun} title="Latest scrape" />
        </div>
      </div>

      <div className="mt-6">
        <RecentJobsPanel
          jobs={recentJobs}
          selectedId={selectedJobId}
          onSelect={setSelectedJobId}
          stopping={stopping}
          onStop={(id) => void stopJob(id)}
        />
      </div>

      {selectedJob && (
        <div className="mt-6">
          <ScraperLogConsole
            key={selectedJob.id}
            taskId={selectedJob.id}
            title={`Live ${selectedJob.kind} log`}
            path={
              selectedJob.kind === "scrape"
                ? "/api/v1/admin/scraper/runs/{taskId}/logs"
                : "/api/v1/admin/pipeline/runs/{taskId}/logs"
            }
            idleMessage="Select a job above to stream its logs."
          />
        </div>
      )}

      <div className="mt-6">
        <TrendContentTable latestJobId={latestRun?.id ?? null} />
      </div>
    </main>
  );
}

function PipelineFunnelHint({ stats }: { stats: PipelineStats }) {
  const base = "mt-4 rounded-lg p-3 text-sm";
  if (stats.failed > 0 || stats.needs_intervention > 0) {
    return (
      <div className={`${base} bg-red-50 text-red-700`}>
        <strong>Attention:</strong> {stats.failed} failed and {stats.needs_intervention} need
        intervention. Check the recent jobs log for the error and click a failed job to see details.
      </div>
    );
  }
  if (stats.discovered === 0) {
    return (
      <div className={`${base} bg-slate-50 text-slate-600`}>
        No discovered content yet. Start a scrape or wait for the next scheduled run.
      </div>
    );
  }
  if (stats.enriched === 0) {
    return (
      <div className={`${base} bg-amber-50 text-amber-700`}>
        {stats.discovered} discovered items need <strong>Enrich</strong> to score and transcribe.
      </div>
    );
  }
  if (stats.embedded === 0) {
    return (
      <div className={`${base} bg-amber-50 text-amber-700`}>
        {stats.enriched} enriched items are ready to embed. If <strong>Embed</strong> keeps returning
        0, the items likely have no <code>video_url</code> (common with the Meta API public media
        endpoint).
      </div>
    );
  }
  return (
    <div className={`${base} bg-green-50 text-green-700`}>
      Pipeline complete: {stats.embedded} items embedded and ready for search/recommendation.
    </div>
  );
}

function RecentJobsPanel({
  jobs,
  selectedId,
  onSelect,
  onStop,
  stopping,
}: {
  jobs: Job[];
  selectedId: string | null;
  onSelect: (id: string) => void;
  onStop: (id: string) => void;
  stopping: string | null;
}) {
  return (
    <section className="card" aria-labelledby="recent-jobs-heading">
      <div className="card-header">
        <h2 id="recent-jobs-heading" className="section-title">
          Recent jobs
        </h2>
        <p className="mt-1 text-sm text-slate-500">
          Click a row to stream its live log. New jobs appear automatically.
        </p>
      </div>
      <div className="p-5 sm:p-6">
        {jobs.length === 0 ? (
          <p className="text-sm text-slate-500">No jobs yet.</p>
        ) : (
          <div className="overflow-x-auto rounded-xl border border-slate-100">
            <table className="w-full text-left text-sm">
              <thead className="bg-slate-50 text-slate-600">
                <tr>
                  <th className="px-4 py-3 font-medium">Kind</th>
                  <th className="px-4 py-3 font-medium">State</th>
                  <th className="px-4 py-3 font-medium">Started</th>
                  <th className="px-4 py-3 font-medium">Duration</th>
                  <th className="px-4 py-3 font-medium">Counters</th>
                  <th className="px-4 py-3 font-medium">Error</th>
                  <th className="px-4 py-3 font-medium">Actions</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100">
                {jobs.map((job) => (
                  <tr
                    key={job.id}
                    className={`cursor-pointer hover:bg-slate-50 ${
                      job.id === selectedId ? "bg-slate-100" : ""
                    }`}
                    onClick={() => onSelect(job.id)}
                  >
                    <td className="px-4 py-3 text-slate-700">{job.kind}</td>
                    <td className="px-4 py-3">
                      <StatusBadge status={job.state} />
                    </td>
                    <td className="px-4 py-3 text-slate-600">
                      {formatDate(job.started_at ?? job.created_at)}
                    </td>
                    <td className="px-4 py-3 text-slate-600">
                      {formatDuration(job.started_at, job.finished_at)}
                    </td>
                    <td className="px-4 py-3 text-slate-600">
                      {Object.entries(job.counters ?? {})
                        .filter(([, value]) => value > 0)
                        .map(([key, value]) => `${key}: ${value}`)
                        .join(", ") || "—"}
                    </td>
                    <td className="px-4 py-3">
                      {job.error ? (
                        <span className="text-red-600" title={job.error}>
                          {job.error.slice(0, 60)}
                          {job.error.length > 60 ? "…" : ""}
                        </span>
                      ) : (
                        "—"
                      )}
                    </td>
                    <td className="px-4 py-3">
                      {isRunActive(job) && (
                        <button
                          type="button"
                          className="button button-secondary"
                          disabled={stopping === job.id}
                          onClick={(event) => {
                            event.stopPropagation();
                            onStop(job.id);
                          }}
                        >
                          {stopping === job.id && <span className="spinner" />}
                          Stop
                        </button>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </section>
  );
}

function PipelinePanel({
  stats,
  run,
  busy,
  active,
  fullBusy,
  stopping,
  onRun,
  onRunFull,
  onStop,
}: {
  stats: PipelineStats | null;
  run: Job | null;
  busy: PipelineStage | null;
  active: boolean;
  fullBusy: boolean;
  stopping: string | null;
  onRun: (stage: PipelineStage) => void;
  onRunFull: () => void;
  onStop: (id: string) => void;
}) {
  const stages: PipelineStage[] = ["enrich", "embed"];
  const fullDisabled = fullBusy || busy !== null || active;
  return (
    <section className="card" aria-labelledby="pipeline-heading">
      <div className="card-header">
        <h2 id="pipeline-heading" className="section-title">
          Processing pipeline
        </h2>
        <p className="mt-1 text-sm text-slate-500">
          Scrape only discovers/upserts content. Click <strong>Run full pipeline</strong> or run
          Enrich → Embed to move content through metadata and embedding.
        </p>
      </div>
      <div className="p-5 sm:p-6">
        {stats && (
          <div className="grid grid-cols-3 gap-3">
            <Metric label="Discovered" value={stats.discovered} />
            <Metric label="Enriched" value={stats.enriched} />
            <Metric label="Stored" value={stats.stored} />
            <Metric label="Embedded" value={stats.embedded} />
          </div>
        )}
        {stats && <PipelineFunnelHint stats={stats} />}
        <div className="mt-5 flex flex-wrap gap-2">
          <button
            type="button"
            className="button button-primary"
            disabled={fullDisabled}
            onClick={() => onRunFull()}
          >
            {fullBusy && <span className="spinner spinner-light" />}
            {fullBusy ? "Starting pipeline..." : "Run full pipeline"}
          </button>
          {stages.map((stage) => (
            <button
              key={stage}
              type="button"
              className="button button-secondary"
              disabled={fullDisabled || busy !== null || active}
              onClick={() => onRun(stage)}
            >
              {busy === stage && <span className="spinner" />}
              {STAGE_LABELS[stage]}
            </button>
          ))}
        </div>
        {active && run?.id && (
          <div className="mt-3 flex flex-wrap gap-2">
            <button
              type="button"
              className="button button-secondary"
              disabled={stopping === run.id}
              onClick={() => onStop(run.id)}
            >
              {stopping === run.id && <span className="spinner" />}
              Stop {run.kind} job
            </button>
          </div>
        )}
        {run && (
          <div className="mt-5 border-t border-slate-100 pt-5">
            <div className="flex items-center justify-between gap-3">
              <p className="text-sm font-medium text-slate-700">
                Latest {run.kind} job
              </p>
              <StatusBadge status={run.state} />
            </div>
            <CounterGrid counters={run.counters} />
            {run.error && (
              <p className="mt-3 rounded-lg bg-red-50 p-3 text-sm text-red-700">
                {run.error}
              </p>
            )}
          </div>
        )}
      </div>
    </section>
  );
}

function RunPanel({ run, title }: { run: Job | null; title: string }) {
  const errors = runErrors(run);
  return (
    <section className="card self-start" aria-labelledby="run-heading">
      <div className="card-header flex items-start justify-between gap-3">
        <div>
          <h2 id="run-heading" className="section-title">
            {title}
          </h2>
          <p className="mt-1 text-sm text-slate-500">
            {run?.id ? `Job ${run.id.slice(0, 8)}` : "No runs have been started."}
          </p>
        </div>
        {run && <StatusBadge status={run.state} />}
      </div>
      {run ? (
        <div className="p-5 sm:p-6">
          <CounterGrid counters={run.counters} />
          {(run.started_at || run.created_at) && (
            <dl className="mt-6 space-y-2 border-t border-slate-100 pt-5 text-sm">
              <TimeRow label="Started" value={run.started_at ?? run.created_at} />
              <TimeRow label="Finished" value={run.finished_at} />
            </dl>
          )}
          {errors.length > 0 && (
            <div className="mt-5 rounded-xl bg-red-50 p-4">
              <h3 className="text-sm font-semibold text-red-800">Run errors</h3>
              <ul className="mt-2 list-disc space-y-1 pl-5 text-sm text-red-700">
                {errors.map((item, index) => (
                  <li key={`${item}-${index}`}>{item}</li>
                ))}
              </ul>
            </div>
          )}
        </div>
      ) : (
        <div className="p-8 text-center text-sm text-slate-500">
          Start a run to see live progress and counters here.
        </div>
      )}
    </section>
  );
}

function CounterGrid({ counters }: { counters: Record<string, number> }) {
  const entries = Object.entries(counters ?? {});
  if (!entries.length) {
    return (
      <p className="mt-3 text-sm text-slate-400">No counters reported yet.</p>
    );
  }
  return (
    <div className="mt-3 grid grid-cols-2 gap-3">
      {entries.map(([label, value]) => (
        <Metric key={label} label={label.replaceAll("_", " ")} value={value} />
      ))}
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

function TimeRow({ label, value }: { label: string; value?: string | null }) {
  return (
    <div className="flex justify-between gap-4">
      <dt className="text-slate-500">{label}</dt>
      <dd className="text-right font-medium text-slate-700">
        {value ? new Date(value).toLocaleString() : "—"}
      </dd>
    </div>
  );
}

function formatDate(value?: string | null): string {
  if (!value) return "—";
  return new Date(value).toLocaleString();
}

function formatDuration(start?: string | null, finish?: string | null): string {
  if (!start || !finish) return "—";
  const seconds = Math.max(
    0,
    Math.round((new Date(finish).getTime() - new Date(start).getTime()) / 1000),
  );
  if (seconds < 60) return `${seconds}s`;
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m ${seconds % 60}s`;
  const hours = Math.floor(seconds / 3600);
  const minutes = Math.floor((seconds % 3600) / 60);
  return `${hours}h ${minutes}m`;
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
