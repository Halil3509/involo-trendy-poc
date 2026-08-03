"use client";

import { FormEvent, useEffect, useState } from "react";

import { ApiError, api } from "@/lib/api";
import type {
  AdminObservability as Observability,
  EvaluationRun,
  EvaluationRunSnapshot,
} from "@/lib/types";

export function AdminObservability() {
  const [data, setData] = useState<Observability | null>(null);
  const [error, setError] = useState("");
  const [runError, setRunError] = useState("");
  const [runMessage, setRunMessage] = useState("");
  const [running, setRunning] = useState(false);
  const [modelVersion, setModelVersion] = useState("");
  const [dataCutoff, setDataCutoff] = useState(() =>
    new Date().toISOString().slice(0, 16),
  );
  const [k, setK] = useState(10);

  useEffect(() => {
    let active = true;
    api
      .getAdminObservability()
      .then((result) => {
        if (active) setData(result);
      })
      .catch((caught) => {
        if (active) {
          setError(
            caught instanceof Error
              ? caught.message
              : "Unable to load observability.",
          );
        }
      });
    return () => {
      active = false;
    };
  }, []);

  async function runEvaluation(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!modelVersion.trim() || !dataCutoff) return;
    setRunning(true);
    setRunError("");
    setRunMessage("");
    try {
      const evaluation = await api.runOfflineEvaluation({
        model_version: modelVersion.trim(),
        data_cutoff: new Date(dataCutoff).toISOString(),
        k,
      });
      setData((current) =>
        current
          ? {
              ...current,
              evaluation: {
                ...current.evaluation,
                latest: toSnapshot(evaluation),
              },
            }
          : current,
      );
      setRunMessage(
        evaluation.passed
          ? "Offline evaluation passed all quality gates."
          : "Offline evaluation completed but did not pass all quality gates.",
      );
    } catch (caught) {
      if (
        caught instanceof ApiError &&
        caught.status === 409 &&
        caught.message.toLowerCase().includes("no labeled historical")
      ) {
        setRunError(
          "No historical labeled rankings are available for this model and cutoff. Collect later outcome snapshots or labels, then retry.",
        );
      } else {
        setRunError(
          caught instanceof Error
            ? caught.message
            : "Unable to run the offline evaluation.",
        );
      }
    } finally {
      setRunning(false);
    }
  }

  return (
    <section className="card mt-6" aria-labelledby="observability-heading">
      <div className="card-header">
        <h2 id="observability-heading" className="section-title">
          Production observability
        </h2>
        <p className="mt-1 text-sm text-slate-500">
          Pipeline latency, provider telemetry, budget, and offline quality gates.
        </p>
      </div>
      {error ? (
        <div className="alert alert-error m-5" role="alert">
          {error}
        </div>
      ) : !data ? (
        <p className="p-6 text-sm text-slate-500" role="status">
          Loading observability...
        </p>
      ) : (
        <div className="space-y-7 p-5 sm:p-6">
          <dl className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
            <Metric
              label="Oldest queued job"
              value={formatDuration(data.queue_age_seconds)}
              warning={(data.queue_age_seconds ?? 0) > 300}
            />
            <Metric
              label="Job duration p50"
              value={formatDuration(data.job_duration_p50_seconds)}
            />
            <Metric
              label="Job duration p95"
              value={formatDuration(data.job_duration_p95_seconds)}
            />
            <Metric
              label="Snapshot coverage"
              value={`${Math.round(data.snapshot_coverage * 100)}%`}
              warning={data.snapshot_coverage < 0.8}
            />
            <Metric
              label="Estimated provider cost"
              value={formatCurrency(data.provider_usage.totals.estimated_cost)}
            />
            <Metric
              label="Input tokens"
              value={data.provider_usage.totals.input_tokens.toLocaleString()}
            />
            <Metric
              label="Output tokens"
              value={data.provider_usage.totals.output_tokens.toLocaleString()}
            />
            <Metric
              label="Stale trends / profiles"
              value={`${data.stale_trends} / ${data.stale_profiles}`}
              warning={data.stale_trends + data.stale_profiles > 0}
            />
          </dl>

          <div className="grid gap-6 lg:grid-cols-2">
            <RecordPanel title="Recommendation funnel" values={data.funnel} />
            <RecordPanel
              title="Multimodal failures"
              values={data.multimodal_failures}
              empty="No multimodal failures."
            />
          </div>

          <ProviderTelemetry data={data} />

          <div className="grid gap-6 lg:grid-cols-[1.25fr_0.75fr]">
            <EvaluationPanel
              evaluation={data.evaluation.latest}
              configuredThresholds={data.evaluation.thresholds}
            />
            <form
              className="rounded-xl border border-slate-200 p-5"
              aria-labelledby="run-evaluation-heading"
              onSubmit={runEvaluation}
            >
              <h3 id="run-evaluation-heading" className="section-title">
                Run offline evaluation
              </h3>
              <p className="mt-1 text-sm leading-6 text-slate-500">
                Evaluate historical rankings without affecting production traffic.
              </p>
              {runError && (
                <div className="alert alert-error mt-4" role="alert">
                  {runError}
                </div>
              )}
              {runMessage && (
                <div className="alert alert-success mt-4" role="status">
                  {runMessage}
                </div>
              )}
              <div className="mt-4 space-y-4">
                <div>
                  <label className="label" htmlFor="evaluation-model">
                    Model version
                  </label>
                  <input
                    className="input"
                    id="evaluation-model"
                    required
                    maxLength={200}
                    value={modelVersion}
                    placeholder="ranking-v2"
                    onChange={(event) => setModelVersion(event.target.value)}
                  />
                </div>
                <div>
                  <label className="label" htmlFor="evaluation-cutoff">
                    Data cutoff
                  </label>
                  <input
                    className="input"
                    id="evaluation-cutoff"
                    type="datetime-local"
                    required
                    value={dataCutoff}
                    onChange={(event) => setDataCutoff(event.target.value)}
                  />
                </div>
                <div>
                  <label className="label" htmlFor="evaluation-k">
                    Ranking depth (k)
                  </label>
                  <input
                    className="input"
                    id="evaluation-k"
                    type="number"
                    min={1}
                    max={100}
                    required
                    value={k}
                    onChange={(event) => setK(Number(event.target.value))}
                  />
                </div>
              </div>
              <button
                className="button button-primary mt-5 w-full"
                disabled={running}
              >
                {running && (
                  <span className="spinner spinner-light" aria-hidden="true" />
                )}
                {running ? "Running evaluation..." : "Run evaluation"}
              </button>
            </form>
          </div>
        </div>
      )}
    </section>
  );
}

function ProviderTelemetry({ data }: { data: Observability }) {
  return (
    <section aria-labelledby="provider-telemetry-heading">
      <h3 id="provider-telemetry-heading" className="section-title">
        Provider, model, and stage telemetry
      </h3>
      {data.provider_usage.groups.length ? (
        <div className="mt-3 overflow-x-auto rounded-xl border border-slate-200">
          <table className="data-table">
            <caption className="sr-only">
              Provider telemetry grouped by provider, model, and processing stage
            </caption>
            <thead>
              <tr>
                <th scope="col">Provider</th>
                <th scope="col">Model</th>
                <th scope="col">Stage</th>
                <th scope="col">Runs</th>
                <th scope="col">Failures</th>
                <th scope="col">Avg latency</th>
                <th scope="col">Media</th>
              </tr>
            </thead>
            <tbody>
              {data.provider_usage.groups.map((group) => (
                <tr key={`${group.provider}-${group.model_id}-${group.stage}`}>
                  <td>{group.provider}</td>
                  <td>{group.model_id}</td>
                  <td>{group.stage}</td>
                  <td>{group.runs}</td>
                  <td className={group.failures ? "text-red-700" : ""}>
                    {group.failures}
                  </td>
                  <td>{Math.round(group.average_duration_ms).toLocaleString()}ms</td>
                  <td>{Math.round(group.media_seconds)}s</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <p className="mt-3 text-sm text-slate-500">
          No provider runs have been recorded.
        </p>
      )}
    </section>
  );
}

function EvaluationPanel({
  evaluation,
  configuredThresholds,
}: {
  evaluation: EvaluationRunSnapshot | null;
  configuredThresholds: Observability["evaluation"]["thresholds"];
}) {
  if (!evaluation) {
    return (
      <section
        className="rounded-xl border border-dashed border-slate-300 p-5"
        aria-labelledby="evaluation-heading"
      >
        <h3 id="evaluation-heading" className="section-title">
          Latest offline evaluation
        </h3>
        <p className="mt-2 text-sm text-slate-500">
          No evaluation has been run yet. Current minimum NDCG gate:{" "}
          {formatDecimal(configuredThresholds.min_ndcg_at_k)}.
        </p>
      </section>
    );
  }
  const gates = [
    {
      label: `NDCG@${evaluation.k}`,
      value: evaluation.metrics.ndcg_at_k,
      threshold: evaluation.thresholds.min_ndcg_at_k,
      passed:
        evaluation.metrics.ndcg_at_k >=
        evaluation.thresholds.min_ndcg_at_k,
      direction: "minimum",
    },
    {
      label: `Precision@${evaluation.k}`,
      value: evaluation.metrics.precision_at_k,
      threshold: evaluation.thresholds.min_precision_at_k,
      passed:
        evaluation.metrics.precision_at_k >=
        evaluation.thresholds.min_precision_at_k,
      direction: "minimum",
    },
    {
      label: "Brier score",
      value: evaluation.metrics.brier,
      threshold: evaluation.thresholds.max_brier,
      passed: evaluation.metrics.brier <= evaluation.thresholds.max_brier,
      direction: "maximum",
    },
    {
      label: "P95 latency",
      value: evaluation.metrics.p95_latency_seconds ?? 0,
      threshold: evaluation.thresholds.max_p95_latency_seconds,
      passed:
        (evaluation.metrics.p95_latency_seconds ?? 0) <=
        evaluation.thresholds.max_p95_latency_seconds,
      direction: "maximum",
      suffix: "s",
    },
    {
      label: "Cost / prediction",
      value: evaluation.metrics.cost_per_prediction,
      threshold: evaluation.thresholds.max_cost_per_prediction,
      passed:
        evaluation.metrics.cost_per_prediction <=
        evaluation.thresholds.max_cost_per_prediction,
      direction: "maximum",
      currency: true,
    },
  ];
  return (
    <section
      className="rounded-xl border border-slate-200 p-5"
      aria-labelledby="evaluation-heading"
    >
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h3 id="evaluation-heading" className="section-title">
            Latest offline evaluation
          </h3>
          <p className="mt-1 text-sm text-slate-500">
            {evaluation.model_version} · {evaluation.sample_size} rankings /{" "}
            {evaluation.candidate_sample_size} candidates
          </p>
        </div>
        <span
          className={`status ${
            evaluation.passed ? "status-success" : "status-danger"
          }`}
        >
          {evaluation.passed ? "Quality gates passed" : "Quality gates failed"}
        </span>
      </div>
      <dl className="mt-5 grid gap-3 sm:grid-cols-2">
        {gates.map((gate) => (
          <div className="metric" key={gate.label}>
            <dt>{gate.label}</dt>
            <dd className="text-base!">
              {gate.currency
                ? formatCurrency(gate.value)
                : `${formatDecimal(gate.value)}${gate.suffix ?? ""}`}
            </dd>
            <p
              className={`mt-1 text-xs font-semibold ${
                gate.passed ? "text-emerald-700" : "text-red-700"
              }`}
            >
              {gate.passed ? "Pass" : "Fail"} · {gate.direction}{" "}
              {gate.currency
                ? formatCurrency(gate.threshold)
                : `${formatDecimal(gate.threshold)}${gate.suffix ?? ""}`}
            </p>
          </div>
        ))}
      </dl>
      <div
        className={`alert mt-4 ${
          evaluation.rollback_recommended ? "alert-error" : "alert-success"
        }`}
        role="status"
      >
        {evaluation.rollback_recommended
          ? "Rollback recommended: quality regressed beyond configured tolerances."
          : "Rollback not recommended for this evaluation."}
      </div>
      <p className="mt-3 text-xs leading-5 text-slate-500">
        Labels: {evaluation.label_definition}
      </p>
      <p className="mt-1 text-xs text-slate-400">
        Evaluated{" "}
        <time dateTime={evaluation.created_at}>
          {new Date(evaluation.created_at).toLocaleString()}
        </time>
      </p>
    </section>
  );
}

function RecordPanel({
  title,
  values,
  empty = "No activity in this window.",
}: {
  title: string;
  values: Record<string, number>;
  empty?: string;
}) {
  const entries = Object.entries(values);
  return (
    <section>
      <h3 className="text-sm font-semibold text-slate-900">{title}</h3>
      {entries.length ? (
        <dl className="mt-3 space-y-2">
          {entries.map(([label, value]) => (
            <div
              className="flex justify-between rounded-lg bg-slate-50 p-3 text-sm"
              key={label}
            >
              <dt className="capitalize text-slate-600">
                {label.replaceAll("_", " ")}
              </dt>
              <dd className="font-semibold text-slate-900">
                {value.toLocaleString()}
              </dd>
            </div>
          ))}
        </dl>
      ) : (
        <p className="mt-3 text-sm text-slate-500">{empty}</p>
      )}
    </section>
  );
}

function Metric({
  label,
  value,
  warning = false,
}: {
  label: string;
  value: string | number;
  warning?: boolean;
}) {
  return (
    <div
      className={`metric ${warning ? "border-amber-200! bg-amber-50!" : ""}`}
    >
      <dt>{label}</dt>
      <dd>{value}</dd>
    </div>
  );
}

function toSnapshot(evaluation: EvaluationRun): EvaluationRunSnapshot {
  const { id, ...snapshot } = evaluation;
  return { ...snapshot, _id: id };
}

function formatDuration(seconds: number | null): string {
  if (seconds == null) return "—";
  return seconds < 60
    ? `${Math.round(seconds)}s`
    : `${Math.round(seconds / 60)}m`;
}

function formatCurrency(value: number): string {
  return new Intl.NumberFormat("en-US", {
    style: "currency",
    currency: "USD",
    maximumFractionDigits: 4,
  }).format(value);
}

function formatDecimal(value: number): string {
  return value.toFixed(3);
}
