"use client";

import type { Job } from "@/lib/types";
import { isRunActive } from "@/lib/validation";

const PHASE_LABELS: Record<string, string> = {
  queued: "Sıraya alındı",
  running: "Başlatılıyor",
  fetching: "Gönderiler getiriliyor",
  fetched: "Gönderiler toplandı, analiz başlıyor",
  analyzing: "AI gönderileri analiz ediyor",
  reporting: "Marka raporu hazırlanıyor",
  analyzed: "Rapor hazırlandı",
  succeeded: "Tamamlandı",
  failed: "Analiz başarısız oldu",
  cancelled: "İptal edildi",
  needs_intervention: "Müdahale gerekiyor",
  skipped_locked: "Kilitli - atlandı",
};

function isErrorState(state: string): boolean {
  return ["failed", "cancelled"].includes(state.toLowerCase());
}

function isSuccessState(state: string): boolean {
  return ["analyzed", "succeeded"].includes(state.toLowerCase());
}

function computePercent(job: Job): number {
  const state = job.state.toLowerCase();
  const counters = job.counters ?? {};
  const total = Number(counters.total ?? counters.requested ?? 0);

  if (state === "queued" || state === "running") return 5;
  if (state === "fetching") {
    const fetched = Number(counters.fetched ?? 0);
    return total > 0 ? 5 + Math.floor((fetched / total) * 45) : 5;
  }
  if (state === "fetched") return 50;
  if (state === "analyzing") {
    const analyzed = Number(counters.analyzed ?? 0);
    return total > 0 ? 50 + Math.floor((analyzed / total) * 45) : 50;
  }
  if (state === "reporting") return 95;
  if (isSuccessState(state)) return 100;
  if (isErrorState(state)) {
    const analyzed = Number(counters.analyzed ?? 0);
    const fetched = Number(counters.fetched ?? 0);
    if (analyzed > 0 && total > 0) {
      return 50 + Math.floor((analyzed / total) * 45);
    }
    if (fetched > 0 && total > 0) {
      return 5 + Math.floor((fetched / total) * 45);
    }
    return 5;
  }
  return 0;
}

function barColor(state: string): string {
  if (isSuccessState(state)) return "bg-green-600";
  if (isErrorState(state)) return "bg-red-600";
  if (state.toLowerCase() === "needs_intervention") return "bg-yellow-500";
  return "bg-indigo-600";
}

type CounterItemProps = {
  label: string;
  value: number | string;
};

function CounterItem({ label, value }: CounterItemProps) {
  return (
    <div className="rounded-lg bg-slate-50 p-3 text-center">
      <p className="text-2xl font-semibold text-slate-900">{value}</p>
      <p className="text-xs text-slate-500">{label}</p>
    </div>
  );
}

type BrandAnalysisProgressProps = {
  job: Job;
  isStopping?: boolean;
  onStop?: () => void;
};

export function BrandAnalysisProgress({
  job,
  isStopping = false,
  onStop,
}: BrandAnalysisProgressProps) {
  const state = job.state.toLowerCase();
  const percent = computePercent(job);
  const counters = job.counters ?? {};
  const fetched = Number(counters.fetched ?? 0);
  const analyzed = Number(counters.analyzed ?? 0);
  const failed = Number(counters.failed ?? 0);
  const total = Number(counters.total ?? counters.requested ?? 0);
  const requested = Number(counters.requested ?? total);

  return (
    <section className="card" aria-labelledby="brand-progress-heading">
      <div className="card-header">
        <h2 id="brand-progress-heading" className="section-title">
          Analiz durumu
        </h2>
        <p className="mt-1 text-sm text-slate-500">
          {job.target_username
            ? `@${job.target_username} için ilerleme`
            : "İşlem ilerlemesi"}
        </p>
      </div>
      <div className="p-5 sm:p-6">
        <div className="flex items-center justify-between gap-3">
          <p className="font-medium text-slate-900">
            {PHASE_LABELS[state] ?? state}
          </p>
          <div className="flex items-center gap-3">
            {isRunActive(job) && onStop && (
              <button
                type="button"
                className="button button-secondary text-sm"
                onClick={onStop}
                disabled={isStopping}
                aria-label="Analizi durdur"
              >
                {isStopping && <span className="spinner" />}
                Durdur
              </button>
            )}
            <span className="text-sm font-medium text-slate-600">%{percent}</span>
          </div>
        </div>
        <div
          className="mt-3 h-3 w-full overflow-hidden rounded-full bg-slate-200"
          role="progressbar"
          aria-valuenow={percent}
          aria-valuemin={0}
          aria-valuemax={100}
          aria-label="Brand analysis progress"
        >
          <div
            className={`h-full transition-all duration-500 ${barColor(state)}`}
            style={{ width: `${percent}%` }}
          />
        </div>
        {job.error && (
          <p className="mt-3 rounded-lg bg-red-50 p-3 text-sm text-red-700">
            {job.error}
          </p>
        )}
        <div className="mt-5 grid grid-cols-2 gap-3 sm:grid-cols-4">
          <CounterItem label="Toplanan" value={fetched} />
          <CounterItem label="Analiz edilen" value={analyzed} />
          <CounterItem label="Hata" value={failed} />
          <CounterItem label="Toplam" value={total} />
        </div>
        {requested !== total && (
          <p className="mt-3 text-xs text-slate-400">
            İstenen gönderi: {requested}
          </p>
        )}
      </div>
    </section>
  );
}
