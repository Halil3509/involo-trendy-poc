"use client";

import { AppShell } from "@/components/app-shell";
import { CreatorsDashboard } from "@/components/creators-dashboard";

export default function CreatorsPage() {
  return (
    <AppShell>
      <main className="page-container">
        <div className="mb-8">
          <p className="eyebrow">Creator tracking</p>
          <h1 className="page-title mt-2">
            Creators<span className="text-indigo-600">.</span>
          </h1>
          <p className="mt-2 text-sm leading-6 text-slate-500">
            Daily snapshots, viral scoring, and AI profiles for the public
            creators you follow.
          </p>
        </div>
        <CreatorsDashboard />
      </main>
    </AppShell>
  );
}
