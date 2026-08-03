"use client";

import { useCallback, useEffect, useState } from "react";

import { InstagramProfileCard } from "@/components/instagram-profile-card";
import { ProfileAnalytics } from "@/components/profile-analytics";
import { useInstagramStatus } from "@/components/use-instagram-status";
import { api } from "@/lib/api";
import type {
  RecommendationBatch,
  RecommendationContentFormat,
} from "@/lib/types";

const FORMAT_LABELS: Record<RecommendationContentFormat, string> = {
  reels: "Reels",
  carousel: "Carousel",
  native_photo: "Native photo",
};

export function ProfileAnalysis() {
  const instagram = useInstagramStatus();
  const [history, setHistory] = useState<RecommendationBatch[]>([]);
  const [historyLoading, setHistoryLoading] = useState(true);

  const loadHistory = useCallback(async () => {
    try {
      setHistory(await api.getRecommendations(10));
    } catch {
      setHistory([]);
    } finally {
      setHistoryLoading(false);
    }
  }, []);

  useEffect(() => {
    const timer = window.setTimeout(() => void loadHistory(), 0);
    return () => window.clearTimeout(timer);
  }, [loadHistory]);

  return (
    <main className="page-container">
      <div className="mb-8">
        <p className="eyebrow">Personalization</p>
        <h1 className="page-title mt-2">Your creator profile</h1>
        <p className="mt-2 max-w-2xl text-sm leading-6 text-slate-500">
          A living analysis of your content style, built from your Instagram
          activity and used to tailor recommendations.
        </p>
      </div>

      <div>
        <InstagramProfileCard
          status={instagram.status}
          loading={instagram.loading}
          statusError={instagram.error}
          refreshStatus={instagram.refresh}
        />
      </div>
      <ProfileAnalytics
        enabled={
          instagram.status?.status === "ready" &&
          instagram.status.analytics_available !== false
        }
      />

      <section className="card mt-6" aria-labelledby="history-heading">
        <div className="card-header">
          <h2 id="history-heading" className="section-title">
            Recommendation history
          </h2>
          <p className="mt-1 text-sm text-slate-500">
            Ideas generated for you over time.
          </p>
        </div>
        <div className="p-5 sm:p-6">
          {historyLoading ? (
            <div className="flex items-center gap-3 text-sm text-slate-500" role="status">
              <span className="spinner" aria-hidden="true" />
              Loading history...
            </div>
          ) : history.length ? (
            <div className="space-y-6">
              {history.map((batch) => (
                <div key={batch.id}>
                  <p className="text-sm font-semibold text-slate-700">
                    {new Date(batch.created_at).toLocaleString()}
                  </p>
                  <ul className="mt-3 grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
                    {batch.recommendations.map((item) => (
                      <li
                        key={item.id}
                        className="rounded-xl border border-slate-200 bg-white p-4 shadow-sm"
                      >
                        <div className="flex items-start justify-between gap-2">
                          <h3 className="font-semibold leading-6 text-slate-900">
                            {item.title}
                          </h3>
                          <span className="status status-active shrink-0">
                            {FORMAT_LABELS[item.content_format]}
                          </span>
                        </div>
                        <p className="mt-2 text-sm leading-6 text-slate-600">
                          {item.hook}
                        </p>
                      </li>
                    ))}
                  </ul>
                </div>
              ))}
            </div>
          ) : (
            <p className="text-sm text-slate-500">
              No recommendations have been generated yet.
            </p>
          )}
        </div>
      </section>
    </main>
  );
}
