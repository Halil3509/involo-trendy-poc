"use client";

import {
  ChangeEvent,
  useCallback,
  useEffect,
  useState,
} from "react";

import { api } from "@/lib/api";
import type { TrendContent, TrendContentFilters, TrendContentListResponse, TrendContentStatus } from "@/lib/types";

import { TrendContentDetailDialog } from "./trend-content-detail";

const STATUS_OPTIONS: { value: TrendContentStatus; label: string }[] = [
  { value: "", label: "All statuses" },
  { value: "discovered", label: "Discovered" },
  { value: "enriched", label: "Enriched" },
  { value: "stored", label: "Stored" },
  { value: "embedded", label: "Embedded" },
  { value: "failed", label: "Failed" },
  { value: "needs_intervention", label: "Needs intervention" },
];

const ACTION_OPTIONS = [
  { value: "", label: "Any action" },
  { value: "inserted", label: "Inserted" },
  { value: "updated", label: "Updated" },
];

const SORT_OPTIONS = [
  { value: "-created_at", label: "Created (newest)" },
  { value: "created_at", label: "Created (oldest)" },
  { value: "-updated_at", label: "Updated (newest)" },
  { value: "updated_at", label: "Updated (oldest)" },
  { value: "-viral_score", label: "Viral score (high)" },
  { value: "viral_score", label: "Viral score (low)" },
];

function StatusBadge({ status }: { status?: string | null }) {
  if (!status) return <span className="status">unknown</span>;
  const normalized = status.toLowerCase();
  const tone =
    normalized === "embedded"
      ? "status-success"
      : normalized === "failed" || normalized === "needs_intervention"
        ? "status-danger"
        : normalized === "enriched"
          ? "status-active"
          : "bg-slate-100 text-slate-700";
  return <span className={`status ${tone}`}>{status}</span>;
}

function formatDate(value?: string | null) {
  if (!value) return "—";
  return new Date(value).toLocaleString();
}

export function TrendContentTable({ latestJobId }: { latestJobId?: string | null }) {
  const [data, setData] = useState<TrendContentListResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [selected, setSelected] = useState<TrendContent | null>(null);

  const [filters, setFilters] = useState<TrendContentFilters>({
    status: undefined,
    action: undefined,
    keyword: undefined,
    search: undefined,
    sort: "-created_at",
    limit: 20,
    offset: 0,
  });

  const [jobFilter, setJobFilter] = useState<string | undefined>(
    latestJobId ?? undefined,
  );

  const load = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const response = await api.getTrendContent({
        ...filters,
        job_id: jobFilter,
      });
      setData(response);
    } catch (caught) {
      setError(
        caught instanceof Error ? caught.message : "Failed to load trend content.",
      );
    } finally {
      setLoading(false);
    }
  }, [filters, jobFilter]);

  useEffect(() => {
    const timer = window.setTimeout(() => void load(), 0);
    return () => window.clearTimeout(timer);
  }, [load]);

  function updateFilter<T extends keyof TrendContentFilters>(
    key: T,
    value: TrendContentFilters[T],
  ) {
    setFilters((current) => ({ ...current, [key]: value, offset: 0 }));
  }

  function handleSearch(event: ChangeEvent<HTMLInputElement>) {
    updateFilter("search", event.target.value || undefined);
  }

  function handleStatus(event: ChangeEvent<HTMLSelectElement>) {
    updateFilter("status", (event.target.value as TrendContentStatus) || undefined);
  }

  function handleAction(event: ChangeEvent<HTMLSelectElement>) {
    const value = event.target.value;
    updateFilter("action", value ? (value as "inserted" | "updated") : undefined);
  }

  function handleKeyword(event: ChangeEvent<HTMLInputElement>) {
    updateFilter("keyword", event.target.value || undefined);
  }

  function handleSort(event: ChangeEvent<HTMLSelectElement>) {
    updateFilter("sort", event.target.value || undefined);
  }

  function goToPage(pageOffset: number) {
    if (pageOffset < 0) return;
    setFilters((current) => ({ ...current, offset: pageOffset }));
  }

  async function openDetail(item: TrendContent) {
    try {
      const detail = await api.getTrendContentDetail(item.id);
      setSelected(detail);
    } catch (caught) {
      setError(
        caught instanceof Error ? caught.message : "Failed to load trend content detail.",
      );
    }
  }

  function toggleLatestJob() {
    if (!latestJobId) return;
    setJobFilter((current) => (current ? undefined : latestJobId));
  }

  const totalPages = data ? Math.ceil(data.total / data.limit) : 0;
  const currentPage = data ? Math.floor(data.offset / data.limit) + 1 : 1;

  return (
    <section className="card" aria-labelledby="trend-content-heading">
      <div className="card-header">
        <h2 id="trend-content-heading" className="section-title">
          Trend content records
        </h2>
        <p className="mt-1 text-sm text-slate-500">
          Inspect every scraped, enriched and embedded record.
        </p>
      </div>

      <div className="p-5 sm:p-6">
        <div className="flex flex-wrap gap-3">
          <div className="flex-1 min-w-[12rem]">
            <label className="label" htmlFor="trend-search">
              Search
            </label>
            <input
              id="trend-search"
              className="input"
              placeholder="caption, owner, shortcode"
              value={filters.search ?? ""}
              onChange={handleSearch}
            />
          </div>

          <div className="w-40">
            <label className="label" htmlFor="trend-status">
              Status
            </label>
            <select id="trend-status" className="input" value={filters.status ?? ""} onChange={handleStatus}>
              {STATUS_OPTIONS.map((option) => (
                <option key={option.value} value={option.value}>
                  {option.label}
                </option>
              ))}
            </select>
          </div>

          <div className="w-40">
            <label className="label" htmlFor="trend-action">
              Action
            </label>
            <select id="trend-action" className="input" value={filters.action ?? ""} onChange={handleAction}>
              {ACTION_OPTIONS.map((option) => (
                <option key={option.value} value={option.value}>
                  {option.label}
                </option>
              ))}
            </select>
          </div>

          <div className="flex-1 min-w-[12rem]">
            <label className="label" htmlFor="trend-keyword">
              Keyword
            </label>
            <input
              id="trend-keyword"
              className="input"
              placeholder="Filter by discovered keyword"
              value={filters.keyword ?? ""}
              onChange={handleKeyword}
            />
          </div>

          <div className="w-48">
            <label className="label" htmlFor="trend-sort">
              Sort
            </label>
            <select id="trend-sort" className="input" value={filters.sort ?? "-created_at"} onChange={handleSort}>
              {SORT_OPTIONS.map((option) => (
                <option key={option.value} value={option.value}>
                  {option.label}
                </option>
              ))}
            </select>
          </div>
        </div>

        {latestJobId && (
          <div className="mt-4 flex items-center gap-2">
            <button
              type="button"
              className={`button text-sm ${jobFilter ? "button-primary" : "button-secondary"}`}
              onClick={toggleLatestJob}
            >
              {jobFilter ? "Showing latest scrape" : "Show latest scrape"}
            </button>
            {jobFilter && (
              <span className="text-xs text-slate-500">
                Job {latestJobId.slice(0, 8)}
              </span>
            )}
          </div>
        )}

        {error && (
          <div className="alert alert-error mt-4" role="alert">
            {error}
          </div>
        )}

        {loading && <div className="mt-4 text-sm text-slate-500">Loading records...</div>}

        {!loading && data && (
          <>
            <div className="mt-4 rounded-lg bg-slate-50 p-3 text-sm text-slate-600">
              {data.total === 0 ? (
                filters.status ? (
                  <>No records with status &quot;{filters.status}&quot;. Try removing filters.</>
                ) : (
                  <>
                    No trend content found. Start a scrape and then run the pipeline. If the scrape
                    succeeds but this table stays empty, check the pipeline log for enrich/embed
                    failures.
                  </>
                )
              ) : (
                <>
                  Showing {data.offset + 1}–
                  {Math.min(data.offset + data.items.length, data.total)} of {data.total} records
                  {filters.status ? ` with status "${filters.status}"` : ""}.
                </>
              )}
            </div>
            <div className="mt-5 overflow-x-auto rounded-xl border border-slate-100">
              <table className="w-full text-left text-sm">
                <thead className="bg-slate-50 text-slate-600">
                  <tr>
                    <th className="px-4 py-3 font-medium">Shortcode</th>
                    <th className="px-4 py-3 font-medium">Owner</th>
                    <th className="px-4 py-3 font-medium">Status</th>
                    <th className="px-4 py-3 font-medium">Action</th>
                    <th className="px-4 py-3 font-medium">Viral score</th>
                    <th className="px-4 py-3 font-medium">Link</th>
                    <th className="px-4 py-3 font-medium">Updated</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-slate-100">
                  {data.items.map((item) => (
                    <tr
                      key={item.id}
                      className="cursor-pointer hover:bg-slate-50"
                      onClick={() => void openDetail(item)}
                    >
                      <td className="px-4 py-3 font-mono text-xs text-slate-700">
                        {item.shortcode ?? "—"}
                      </td>
                      <td className="px-4 py-3 text-slate-700">
                        {item.owner_username ?? "—"}
                      </td>
                      <td className="px-4 py-3">
                        <StatusBadge status={item.processing_status} />
                      </td>
                      <td className="px-4 py-3 capitalize text-slate-600">
                        {item.last_upsert_action ?? "—"}
                      </td>
                      <td className="px-4 py-3 text-slate-700">
                        {item.viral_score?.toFixed(2) ?? "—"}
                      </td>
                      <td className="px-4 py-3">
                        {item.canonical_url ? (
                          <a
                            href={item.canonical_url}
                            target="_blank"
                            rel="noreferrer"
                            className="text-indigo-700 underline"
                            onClick={(event) => event.stopPropagation()}
                          >
                            View on Instagram
                          </a>
                        ) : (
                          "—"
                        )}
                      </td>
                      <td className="px-4 py-3 text-slate-500">
                        {formatDate(item.updated_at)}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>

            {data.items.length === 0 && (
              <p className="mt-4 text-sm text-slate-500">No trend content records match the filters.</p>
            )}

            {data.total > 0 && (
              <div className="mt-4 flex items-center justify-between text-sm text-slate-600">
                <span>
                  {data.offset + 1}–{Math.min(data.offset + data.items.length, data.total)} of {data.total}
                </span>
                <div className="flex gap-2">
                  <button
                    type="button"
                    className="button button-secondary"
                    disabled={data.offset === 0}
                    onClick={() => goToPage(data.offset - data.limit)}
                  >
                    Previous
                  </button>
                  <span className="self-center text-slate-500">
                    Page {currentPage} of {totalPages || 1}
                  </span>
                  <button
                    type="button"
                    className="button button-secondary"
                    disabled={data.offset + data.items.length >= data.total}
                    onClick={() => goToPage(data.offset + data.limit)}
                  >
                    Next
                  </button>
                </div>
              </div>
            )}
          </>
        )}
      </div>

      <TrendContentDetailDialog
        content={selected}
        onClose={() => setSelected(null)}
      />
    </section>
  );
}
