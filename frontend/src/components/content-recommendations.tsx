"use client";

import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";

import { RecommendationCard } from "@/components/recommendation-card";
import { api } from "@/lib/api";
import type {
  ContentRecommendation,
  InstagramStatus,
  RecommendationBatch,
} from "@/lib/types";

type ContentRecommendationsProps = {
  instagramStatus: InstagramStatus | null;
  instagramStatusLoading: boolean;
  instagramStatusError?: string;
  headerAction?: ReactNode;
};

export function ContentRecommendations({
  instagramStatus,
  instagramStatusLoading,
  instagramStatusError = "",
  headerAction,
}: ContentRecommendationsProps) {
  const [batches, setBatches] = useState<RecommendationBatch[]>([]);
  const [loading, setLoading] = useState(true);
  const [generating, setGenerating] = useState(false);
  const [error, setError] = useState("");
  const generatingRef = useRef(false);

  const loadHistory = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      setBatches(await api.getRecommendations(10));
    } catch (caught) {
      setError(toMessage(caught, "Unable to load recommendation history."));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    const timer = window.setTimeout(() => void loadHistory(), 0);
    return () => window.clearTimeout(timer);
  }, [loadHistory]);

  async function generate() {
    if (
      generatingRef.current ||
      loading ||
      instagramStatus?.status !== "ready"
    ) {
      return;
    }

    generatingRef.current = true;
    setGenerating(true);
    setError("");
    try {
      const batch = await api.createRecommendations(3);
      setBatches((current) => [
        batch,
        ...current.filter((item) => item.id !== batch.id),
      ]);
    } catch (caught) {
      setError(toMessage(caught, "Unable to generate recommendations."));
    } finally {
      generatingRef.current = false;
      setGenerating(false);
    }
  }

  const prerequisite = getPrerequisiteMessage(
    instagramStatus,
    instagramStatusLoading,
    instagramStatusError,
  );
  const latest = batches[0];
  const history = batches.slice(1);

  function updateRecommendation(batchId: string, recommendation: ContentRecommendation) {
    setBatches((current) =>
      current.map((batch) =>
        batch.id === batchId
          ? {
              ...batch,
              recommendations: batch.recommendations.map((item) =>
                item.id === recommendation.id ? recommendation : item,
              ),
            }
          : batch,
      ),
    );
  }

  return (
    <section className="mt-6 space-y-6" aria-labelledby="recommendations-heading">
      <div className="card overflow-hidden">
        <div className="card-header flex flex-wrap items-center justify-between gap-4">
          <div>
            <p className="eyebrow">AI studio</p>
            <h2 id="recommendations-heading" className="section-title mt-1">
              Content recommendations
            </h2>
          </div>
          <div className="flex flex-wrap items-center gap-3">
            {headerAction}
            <button
              type="button"
              className="button button-primary"
              disabled={Boolean(prerequisite) || loading || generating}
              onClick={() => void generate()}
            >
              {generating && <span className="spinner spinner-light" aria-hidden="true" />}
              {generating ? "Generating ideas..." : "Generate 3 ideas"}
            </button>
          </div>
        </div>

        <div className="space-y-5 p-5 sm:p-6">
          {prerequisite && (
            <div className="alert border-indigo-200 bg-indigo-50 text-indigo-800">
              {prerequisite}
            </div>
          )}
          {error && (
            <div className="alert alert-error flex flex-wrap items-center justify-between gap-3" role="alert">
              <span>{error}</span>
              {!generating && (
                <button
                  type="button"
                  className="font-semibold underline underline-offset-2"
                  onClick={() => void loadHistory()}
                >
                  Retry history
                </button>
              )}
            </div>
          )}

          {loading ? (
            <div className="flex items-center gap-3 text-sm text-slate-500" role="status">
              <span className="spinner" aria-hidden="true" />
              Loading recommendation history...
            </div>
          ) : latest ? (
            <RecommendationSection
              batch={latest}
              title="Latest recommendations"
              description="Your newest personalized content ideas."
              onRecommendationChange={(item) => updateRecommendation(latest.id, item)}
            />
          ) : (
            <div className="rounded-xl border border-dashed border-slate-300 p-8 text-center">
              <p className="font-medium text-slate-800">No recommendations yet</p>
              <p className="mt-2 text-sm text-slate-500">
                Generate your first set of ideas when your Instagram profile is ready.
              </p>
            </div>
          )}
        </div>
      </div>

      {!loading && history.length > 0 && (
        <div className="card">
          <div className="card-header">
            <h2 className="section-title">Recommendation history</h2>
            <p className="mt-1 text-sm text-slate-500">
              Earlier batches are kept separate from your latest ideas.
            </p>
          </div>
          <div className="space-y-8 p-5 sm:p-6">
            {history.map((batch) => (
              <RecommendationSection
                key={batch.id}
                batch={batch}
                onRecommendationChange={(item) => updateRecommendation(batch.id, item)}
              />
            ))}
          </div>
        </div>
      )}
    </section>
  );
}

function RecommendationSection({
  batch,
  title,
  description,
  onRecommendationChange,
}: {
  batch: RecommendationBatch;
  title?: string;
  description?: string;
  onRecommendationChange: (recommendation: ContentRecommendation) => void;
}) {
  return (
    <section aria-labelledby={`batch-${batch.id}`}>
      <div className="mb-4 flex flex-wrap items-end justify-between gap-2">
        <div>
          <h3 id={`batch-${batch.id}`} className="font-semibold text-slate-900">
            {title ?? formatDate(batch.created_at)}
          </h3>
          {description && <p className="mt-1 text-sm text-slate-500">{description}</p>}
        </div>
        {title && (
          <time className="text-xs font-medium text-slate-500" dateTime={batch.created_at}>
            {formatDate(batch.created_at)}
          </time>
        )}
      </div>
      <div className="grid gap-4 lg:grid-cols-3">
        {batch.recommendations.map((recommendation) => (
          <RecommendationCard
            key={recommendation.id}
            recommendation={recommendation}
            onChange={onRecommendationChange}
          />
        ))}
      </div>
    </section>
  );
}

function getPrerequisiteMessage(
  status: InstagramStatus | null,
  loading: boolean,
  error: string,
): string {
  if (loading) return "Checking whether your Instagram profile is ready...";
  if (error) return `Instagram profile status is unavailable: ${error}`;
  if (!status || status.status === "disconnected") {
    return "Connect Instagram below before generating personalized ideas.";
  }
  if (status.status === "needs_reauth") {
    return "Reconnect Instagram below before generating personalized ideas.";
  }
  if (status.status === "profiling") {
    return "Your Instagram profile is still being analyzed. Ideas will be available when it is ready.";
  }
  if (status.status === "connected") {
    return "Analyze your Instagram profile below before generating personalized ideas.";
  }
  if (status.status === "failed") {
    return "Profile analysis failed. Retry the analysis below before generating ideas.";
  }
  return "";
}

function formatDate(value: string): string {
  return new Date(value).toLocaleString();
}

function toMessage(caught: unknown, fallback: string): string {
  return caught instanceof Error ? caught.message : fallback;
}
