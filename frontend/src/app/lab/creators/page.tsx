import type { Metadata } from "next";

import { CreatorsDashboard } from "@/components/creators-dashboard";

export const metadata: Metadata = {
  title: "Creators · Invo Lab",
};

export default function LabCreatorsPage() {
  return (
    <div>
      <div className="mb-8">
        <p className="eyebrow">Creator tracking</p>
        <h1 className="page-title mt-2">
          Creators<span className="text-indigo-600">.</span>
        </h1>
        <p className="mt-2 max-w-2xl text-sm leading-6 text-slate-500">
          Daily snapshots, viral scoring, and AI profiles for the public
          creators you follow. This tool operates independently from the main
          recommendation pipeline.
        </p>
      </div>
      <CreatorsDashboard />
    </div>
  );
}
