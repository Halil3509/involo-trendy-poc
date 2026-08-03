"use client";

import Link from "next/link";
import { FormEvent, useCallback, useEffect, useState } from "react";

import { ApiError, api } from "@/lib/api";
import type { TrackedCreator } from "@/lib/types";

const STATUS_LABEL: Record<string, string> = {
  active: "Active",
  tracking: "Tracking",
  needs_intervention: "Needs intervention",
  not_found: "Not found",
  failed: "Failed",
};

function statusClass(status: string): string {
  if (status === "active") return "status status-active";
  if (status === "tracking") return "status status-active";
  return "status status-danger";
}

function formatCount(value: number): string {
  if (value >= 1_000_000) return `${(value / 1_000_000).toFixed(1)}M`;
  if (value >= 1_000) return `${(value / 1_000).toFixed(1)}K`;
  return String(value);
}

export function CreatorsDashboard() {
  const [creators, setCreators] = useState<TrackedCreator[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [username, setUsername] = useState("");
  const [adding, setAdding] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);
  const [removingId, setRemovingId] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const items = await api.getTrackedCreators();
      setCreators(items);
      setError(null);
    } catch (caught) {
      setError(
        caught instanceof ApiError
          ? caught.message
          : "Failed to load tracked creators",
      );
    }
    setLoading(false);
  }, []);

  useEffect(() => {
    const timer = window.setTimeout(() => void load(), 0);
    return () => window.clearTimeout(timer);
  }, [load]);

  async function handleAdd(event: FormEvent) {
    event.preventDefault();
    const value = username.trim().replace(/^@/, "");
    if (!value) {
      setFormError("Enter an Instagram username");
      return;
    }
    setAdding(true);
    setFormError(null);
    try {
      await api.addTrackedCreator(value);
      setUsername("");
      await load();
    } catch (caught) {
      setFormError(
        caught instanceof ApiError
          ? caught.message
          : "Failed to add the creator",
      );
    } finally {
      setAdding(false);
    }
  }

  async function handleRemove(creator: TrackedCreator) {
    setRemovingId(creator.id);
    setError(null);
    try {
      await api.removeTrackedCreator(creator.id);
      setCreators((current) =>
        current.filter((item) => item.id !== creator.id),
      );
    } catch (caught) {
      setError(
        caught instanceof ApiError
          ? caught.message
          : "Failed to remove the creator",
      );
    } finally {
      setRemovingId(null);
    }
  }

  return (
    <div>
      <section className="card" aria-labelledby="add-creator-heading">
        <div className="card-header">
          <h2 id="add-creator-heading" className="section-title">
            Track a new creator
          </h2>
        </div>
        <form className="p-5 sm:p-6" onSubmit={handleAdd}>
          <div className="flex flex-col gap-3 sm:flex-row">
            <div className="flex-1">
              <label htmlFor="creator-username" className="sr-only">
                Instagram username
              </label>
              <input
                id="creator-username"
                className="input"
                placeholder="@username"
                value={username}
                onChange={(event) => setUsername(event.target.value)}
                aria-invalid={formError ? "true" : undefined}
                disabled={adding}
              />
              {formError ? (
                <p className="field-error" role="alert">
                  {formError}
                </p>
              ) : (
                <p className="field-hint">
                  Public Instagram profile; snapshots run daily at 03:00 (UTC+3).
                </p>
              )}
            </div>
            <button type="submit" className="button" disabled={adding}>
              {adding ? "Adding…" : "Add creator"}
            </button>
          </div>
        </form>
      </section>

      {error && (
        <div className="alert alert-error mt-6" role="alert">
          {error}
        </div>
      )}

      <section className="card mt-6" aria-labelledby="creators-heading">
        <div className="card-header">
          <h2 id="creators-heading" className="section-title">
            Tracked creators
          </h2>
        </div>
        {loading ? (
          <div className="p-6" role="status">
            Loading tracked creators…
          </div>
        ) : creators.length === 0 ? (
          <div className="p-6 text-sm text-slate-500">
            No creators tracked yet. Add your first creator above to start daily
            snapshots and AI profiling.
          </div>
        ) : (
          <ul className="divide-y divide-slate-100">
            {creators.map((creator) => (
              <li
                key={creator.id}
                className="flex flex-wrap items-center gap-4 p-5 sm:p-6"
              >
                <div className="flex h-11 w-11 shrink-0 items-center justify-center rounded-full bg-indigo-100 text-sm font-semibold uppercase text-indigo-700">
                  {creator.username.slice(0, 2)}
                </div>
                <div className="min-w-0 flex-1">
                  <Link
                    href={`/creators/${creator.id}`}
                    className="font-medium text-slate-900 hover:text-indigo-700"
                  >
                    @{creator.username}
                  </Link>
                  {creator.display_name && (
                    <p className="truncate text-sm text-slate-500">
                      {creator.display_name}
                    </p>
                  )}
                  {creator.status === "needs_intervention" && (
                    <p className="mt-1 text-xs font-medium text-red-600">
                      Instagram verification required — tracking paused.
                    </p>
                  )}
                </div>
                <dl className="flex items-center gap-6 text-sm">
                  <div className="text-right">
                    <dt className="sr-only">Followers</dt>
                    <dd className="font-semibold text-slate-900">
                      {formatCount(creator.follower_count)}
                    </dd>
                    <dd className="text-xs text-slate-500">followers</dd>
                  </div>
                  <div className="text-right">
                    <dt className="sr-only">Trend score</dt>
                    <dd className="font-semibold text-slate-900">
                      {creator.trend_score.toFixed(1)}
                    </dd>
                    <dd className="text-xs text-slate-500">trend score</dd>
                  </div>
                </dl>
                <span className={statusClass(creator.status)}>
                  {STATUS_LABEL[creator.status] ?? creator.status}
                </span>
                <button
                  type="button"
                  className="button button-secondary px-3"
                  disabled={removingId === creator.id}
                  onClick={() => void handleRemove(creator)}
                  aria-label={`Stop tracking @${creator.username}`}
                >
                  {removingId === creator.id ? "Removing…" : "Remove"}
                </button>
              </li>
            ))}
          </ul>
        )}
      </section>
    </div>
  );
}
