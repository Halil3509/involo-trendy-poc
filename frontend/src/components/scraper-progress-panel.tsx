"use client";

import type { JobProgress, JobProgressKeyword } from "@/lib/types";

export function ScraperProgressPanel({
  progress,
}: {
  progress: JobProgress | null | undefined;
}) {
  if (!progress) {
    return (
      <p className="mt-3 text-sm text-slate-400">
        No progress reported yet.
      </p>
    );
  }

  const percent =
    progress.total_target > 0
      ? Math.min(
          100,
          Math.round((progress.total_discovered / progress.total_target) * 100),
        )
      : 0;

  return (
    <div className="mt-5 space-y-4 border-t border-slate-100 pt-5">
      <div className="flex items-center justify-between gap-3">
        <div>
          <p className="text-sm font-medium text-slate-700">Current keyword</p>
          <p className="text-sm text-slate-500">
            {progress.current_keyword ?? "—"}
          </p>
        </div>
        <span className="status status-active">{progress.current_step}</span>
      </div>

      <div>
        <div className="mb-1 flex justify-between text-sm text-slate-600">
          <span>Overall progress</span>
          <span>
            {progress.total_discovered} / {progress.total_target}
          </span>
        </div>
        <div className="h-2 w-full rounded-full bg-slate-100">
          <div
            className="h-2 rounded-full bg-indigo-500 transition-all duration-500"
            style={{ width: `${percent}%` }}
            aria-valuenow={percent}
            aria-valuemin={0}
            aria-valuemax={100}
            role="progressbar"
          />
        </div>
      </div>

      {progress.keywords.length > 0 && (
        <div className="max-h-48 overflow-auto rounded-lg border border-slate-100">
          <table className="w-full text-left text-sm">
            <thead className="bg-slate-50 text-slate-600">
              <tr>
                <th className="px-3 py-2 font-medium">Keyword</th>
                <th className="px-3 py-2 font-medium">Status</th>
                <th className="px-3 py-2 font-medium text-right">Found</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100">
              {progress.keywords.map((keyword: JobProgressKeyword) => (
                <tr key={keyword.name}>
                  <td className="px-3 py-2 text-slate-700">{keyword.name}</td>
                  <td className="px-3 py-2">
                    <KeywordStatusBadge status={keyword.status} />
                  </td>
                  <td className="px-3 py-2 text-right text-slate-600">
                    {keyword.discovered}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

function KeywordStatusBadge({ status }: { status: string }) {
  const normalized = status.toLowerCase();
  const tone =
    normalized === "completed"
      ? "status-success"
      : normalized === "failed"
        ? "status-danger"
        : "status-active";
  return <span className={`status ${tone}`}>{status}</span>;
}
