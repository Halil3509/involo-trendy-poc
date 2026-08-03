"use client";

import { useEffect, useState } from "react";

import { api } from "@/lib/api";
import type { ProfileAnalytics as ProfileAnalyticsData } from "@/lib/types";

export function ProfileAnalytics({ enabled }: { enabled: boolean }) {
  const [analytics, setAnalytics] = useState<ProfileAnalyticsData | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    if (!enabled) return;
    let active = true;
    api
      .getProfileAnalytics()
      .then((data) => active && setAnalytics(data))
      .catch((caught) => {
        if (active) setError(caught instanceof Error ? caught.message : "Unable to load analytics.");
      });
    return () => {
      active = false;
    };
  }, [enabled]);

  if (enabled && !analytics && !error) {
    return <div className="card mt-6 p-6 text-sm text-slate-500" role="status">Loading profile analytics...</div>;
  }
  if (error) return <div className="alert alert-error mt-6" role="alert">{error}</div>;
  if (!analytics) {
    return <section className="card mt-6 p-6" aria-labelledby="analytics-heading"><h2 id="analytics-heading" className="section-title">Profile analytics</h2><p className="mt-2 text-sm text-slate-500">Analyze your Instagram profile to unlock performance insights.</p></section>;
  }

  return (
    <section className="mt-6 space-y-6" aria-labelledby="analytics-heading">
      <div className="card">
        <div className="card-header flex flex-wrap items-end justify-between gap-3">
          <div><p className="eyebrow">Performance model</p><h2 id="analytics-heading" className="section-title mt-1">Profile analytics</h2></div>
          <span className="status status-active">{analytics.schema_version}</span>
        </div>
        <div className="grid gap-3 p-5 sm:grid-cols-2 sm:p-6">
          <div className="metric"><dt>Data quality</dt><dd>{Math.round(analytics.data_quality * 100)}%</dd></div>
          <div className="metric"><dt>Audience markets</dt><dd className="text-base!">{analytics.audience_markets.join(", ") || "Not available"}</dd></div>
        </div>
      </div>

      <section className="card" aria-labelledby="pillars-heading">
        <div className="card-header"><h3 id="pillars-heading" className="section-title">Content pillars</h3><p className="mt-1 text-sm text-slate-500">Structured themes detected across your content.</p></div>
        {analytics.pillars.length ? (
          <ul className="grid gap-4 p-5 md:grid-cols-2 sm:p-6">
            {analytics.pillars.map((pillar) => (
              <li className="rounded-xl border border-slate-200 p-4" key={pillar.id}>
                <div className="flex items-start justify-between gap-3"><h4 className="font-semibold text-slate-900">{pillar.name}</h4><span className="text-xs font-semibold text-slate-500">{Math.round(pillar.confidence * 100)}% confidence</span></div>
                <p className="mt-2 text-sm leading-6 text-slate-600">{pillar.description}</p>
                <dl className="mt-3 grid grid-cols-2 gap-2 text-sm"><div><dt className="text-slate-500">Posts</dt><dd className="font-semibold">{pillar.content_count}</dd></div><div><dt className="text-slate-500">Performance lift</dt><dd className="font-semibold">{pillar.average_performance_residual.toFixed(2)}</dd></div></dl>
                <PatternList label="Strengths" values={pillar.strengths} />
                <PatternList label="Opportunities" values={pillar.opportunities} />
              </li>
            ))}
          </ul>
        ) : <p className="p-6 text-sm text-slate-500">No stable pillars were detected yet.</p>}
      </section>

      <div className="grid gap-6 lg:grid-cols-3">
        <PatternCard title="Winning patterns" values={analytics.winning_patterns} tone="success" />
        <PatternCard title="Losing patterns" values={analytics.losing_patterns} tone="danger" />
        <PatternCard title="Patterns to avoid" values={analytics.avoid_patterns} tone="default" />
      </div>
    </section>
  );
}

function PatternCard({ title, values, tone }: { title: string; values: string[]; tone: "success" | "danger" | "default" }) {
  return <section className="card p-5"><h3 className="section-title">{title}</h3>{values.length ? <ul className={`mt-3 list-inside list-disc space-y-2 text-sm ${tone === "success" ? "text-emerald-700" : tone === "danger" ? "text-red-700" : "text-slate-600"}`}>{values.map((value) => <li key={value}>{value}</li>)}</ul> : <p className="mt-3 text-sm text-slate-500">Not enough evidence yet.</p>}</section>;
}

function PatternList({ label, values }: { label: string; values: string[] }) {
  if (!values.length) return null;
  return <div className="mt-3"><p className="text-xs font-bold uppercase tracking-wider text-slate-400">{label}</p><ul className="mt-1 list-inside list-disc text-sm text-slate-600">{values.map((value) => <li key={value}>{value}</li>)}</ul></div>;
}
