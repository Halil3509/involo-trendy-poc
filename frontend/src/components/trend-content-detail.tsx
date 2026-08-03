"use client";

import {
  MouseEvent,
  useEffect,
  useRef,
} from "react";

import type { TrendContent } from "@/lib/types";

function formatField(label: string, value: unknown) {
  if (value === null || value === undefined) return null;
  if (typeof value === "boolean") return value ? "Yes" : "No";
  if (typeof value === "object") {
    return (
      <pre className="max-h-40 overflow-auto rounded-lg bg-slate-900 p-3 text-xs text-slate-50">
        {JSON.stringify(value, null, 2)}
      </pre>
    );
  }
  return String(value);
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="space-y-3">
      <h3 className="text-sm font-semibold text-slate-900">{title}</h3>
      {children}
    </div>
  );
}

function DetailRow({ label, value }: { label: string; value: unknown }) {
  const formatted = formatField(label, value);
  if (formatted === null) return null;
  return (
    <div className="grid gap-1 sm:grid-cols-[140px_1fr]">
      <dt className="text-sm text-slate-500">{label}</dt>
      <dd className="text-sm text-slate-800 break-words">{formatted}</dd>
    </div>
  );
}

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

export function TrendContentDetailDialog({
  content,
  onClose,
}: {
  content: TrendContent | null;
  onClose: () => void;
}) {
  const dialogRef = useRef<HTMLDialogElement>(null);

  useEffect(() => {
    const dialog = dialogRef.current;
    if (!dialog) return;
    if (content && !dialog.open) {
      dialog.showModal();
    } else if (!content && dialog.open) {
      dialog.close();
    }
  }, [content]);

  function handleBackdrop(event: MouseEvent<HTMLDialogElement>) {
    if (event.target === dialogRef.current) {
      onClose();
    }
  }

  if (!content) return null;

  return (
    <dialog
      ref={dialogRef}
      className="m-auto w-[94vw] max-w-3xl rounded-2xl bg-white p-0 shadow-2xl backdrop:bg-black/50"
      onClick={handleBackdrop}
      onClose={onClose}
    >
      <div className="flex items-start justify-between gap-4 border-b border-slate-100 p-5">
        <div>
          <h2 className="text-lg font-semibold text-slate-900">
            {content.shortcode ? `Post ${content.shortcode}` : "Trend content detail"}
          </h2>
          <p className="mt-1 text-sm text-slate-500">
            {content.owner_username ?? "Unknown author"}
          </p>
        </div>
        <button
          type="button"
          className="button button-secondary"
          onClick={onClose}
          aria-label="Close detail"
        >
          Close
        </button>
      </div>

      <div className="max-h-[80vh] space-y-6 overflow-auto p-5 sm:p-6">
        <div className="flex flex-wrap gap-2">
          <StatusBadge status={content.processing_status} />
          {content.last_upsert_action && (
            <span className="status bg-indigo-50 text-indigo-700">
              {content.last_upsert_action}
            </span>
          )}
        </div>

        <Section title="Identifiers">
          <dl className="space-y-3">
            <DetailRow label="ID" value={content.id} />
            <DetailRow label="Shortcode" value={content.shortcode} />
            <DetailRow label="Media ID" value={content.media_id} />
            <div className="grid gap-1 sm:grid-cols-[140px_1fr]">
              <dt className="text-sm text-slate-500">Canonical URL</dt>
              <dd className="text-sm break-words">
                {content.canonical_url ? (
                  <a
                    href={content.canonical_url}
                    target="_blank"
                    rel="noreferrer"
                    className="font-semibold text-indigo-700 underline"
                  >
                    {content.canonical_url}
                  </a>
                ) : (
                  "—"
                )}
              </dd>
            </div>
            <DetailRow label="Video URL" value={content.video_url} />
            <DetailRow label="Thumbnail URL" value={content.thumbnail_url} />
            <DetailRow label="Source" value={content.source} />
            <DetailRow label="Discovered keywords" value={content.discovered_keywords?.join(", ")} />
          </dl>
        </Section>

        <Section title="Caption & transcript">
          <DetailRow label="Caption" value={content.caption_text} />
          <DetailRow label="Transcript" value={content.transcript} />
          <DetailRow label="Language" value={content.language} />
          <DetailRow label="Combined text" value={content.combined_text} />
        </Section>

        <Section title="Metrics & scoring">
          <dl className="space-y-3">
            <DetailRow label="Viral score" value={content.viral_score} />
            <DetailRow label="Metrics" value={content.metrics} />
            <DetailRow label="Score components" value={content.score_components} />
            <DetailRow label="Duration (seconds)" value={content.duration_seconds} />
            <DetailRow label="Taken at" value={content.taken_at} />
          </dl>
        </Section>

        <Section title="Processing timeline">
          <dl className="space-y-3">
            <DetailRow label="First seen" value={content.first_seen_at} />
            <DetailRow label="Last seen" value={content.last_seen_at} />
            <DetailRow label="Created" value={content.created_at} />
            <DetailRow label="Updated" value={content.updated_at} />
            <DetailRow label="Enriched at" value={content.enriched_at} />
            <DetailRow label="Embedded at" value={content.embedded_at} />
            <DetailRow label="Last scrape job" value={content.last_scrape_job_id} />
            <DetailRow label="Last action" value={content.last_upsert_action} />
          </dl>
        </Section>

        <Section title="Embedding">
          <dl className="space-y-3">
            <DetailRow label="Embedding vector ID" value={content.embedding_vector_id} />
            <DetailRow label="Schema version" value={content.embedding_schema_version} />
            <DetailRow label="Processing regions" value={content.processing_regions} />
          </dl>
        </Section>

        {content.enrichment_error && (
          <div className="rounded-xl bg-red-50 p-4">
            <h3 className="text-sm font-semibold text-red-800">Processing error</h3>
            <p className="mt-1 text-sm text-red-700">{content.enrichment_error}</p>
          </div>
        )}

        <Section title="Raw media payload">
          <DetailRow label="Media asset" value={content.media_asset} />
          <DetailRow label="Keyframes" value={content.keyframes} />
          <DetailRow label="Visual analysis" value={content.visual_analysis} />
          <DetailRow label="Video segments" value={content.video_segments} />
        </Section>
      </div>
    </dialog>
  );
}
