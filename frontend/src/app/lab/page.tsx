import type { Metadata } from "next";

import Link from "next/link";

export const metadata: Metadata = {
  title: "Invo Lab",
};

export default function LabPage() {
  return (
    <div>
      <div className="mb-8">
        <p className="eyebrow">Invo Lab</p>
        <h1 className="page-title mt-2">
          Independent research tools<span className="text-indigo-600">.</span>
        </h1>
        <p className="mt-2 max-w-2xl text-sm leading-6 text-slate-500">
          Standalone tools for tracking creators and analyzing brand profiles.
          Each tool runs independently from the main recommendation pipeline.
        </p>
      </div>

      <div className="grid gap-6 sm:grid-cols-2">
        <Link
          href="/lab/creators"
          className="card block transition hover:border-indigo-200 hover:shadow-md"
        >
          <div className="card-header">
            <h2 className="section-title">Creators</h2>
            <p className="mt-1 text-sm text-slate-500">
              Track public Instagram creators, view follower history, and run AI
              profiling.
            </p>
          </div>
          <div className="p-5 sm:p-6">
            <span className="text-sm font-semibold text-indigo-700">
              Open creators →
            </span>
          </div>
        </Link>

        <Link
          href="/lab/brand-analysis"
          className="card block transition hover:border-indigo-200 hover:shadow-md"
        >
          <div className="card-header">
            <h2 className="section-title">Brand analysis</h2>
            <p className="mt-1 text-sm text-slate-500">
              Analyze any Instagram brand profile, collect recent posts, and
              generate a strategic brief.
            </p>
          </div>
          <div className="p-5 sm:p-6">
            <span className="text-sm font-semibold text-indigo-700">
              Open brand analysis →
            </span>
          </div>
        </Link>
      </div>
    </div>
  );
}
