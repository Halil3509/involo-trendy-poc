"use client";

import Image from "next/image";
import { FormEvent, useCallback, useState } from "react";

import { ApiError, api } from "@/lib/api";
import { BrandAnalysisProgress } from "@/components/brand-analysis-progress";
import { BrandAnalysisReport } from "@/components/brand-analysis-report";
import { ScraperLogConsole } from "@/components/scraper-log-console";
import { usePolling } from "@/lib/hooks";
import type { BrandAnalysisPost, BrandAnalysisReport as BrandAnalysisReportType, Job } from "@/lib/types";
import { isRunActive } from "@/lib/validation";

const BRAND_ANALYSIS_LOGS_PATH = "/api/v1/admin/brand-analysis/runs/{taskId}/logs";

function StatusBadge({ state }: { state: string | null }) {
  const tone: Record<string, string> = {
    queued: "status-active",
    running: "status-active",
    succeeded: "status-success",
    analyzed: "status-success",
    reporting: "status-active",
    failed: "status-danger",
    needs_intervention: "status-warning",
    skipped_locked: "status-warning",
    cancelled: "status-danger",
  };
  return (
    <span className={`status ${tone[state ?? ""] ?? ""}`}>
      {state ?? "idle"}
    </span>
  );
}

export function BrandAnalysisChat() {
  const [url, setUrl] = useState("");
  const [maxPosts, setMaxPosts] = useState(10);
  const [job, setJob] = useState<Job | null>(null);
  const [posts, setPosts] = useState<BrandAnalysisPost[]>([]);
  const [report, setReport] = useState<BrandAnalysisReportType | null>(null);
  const [loading, setLoading] = useState(false);
  const [stopping, setStopping] = useState(false);
  const [error, setError] = useState("");
  const [copied, setCopied] = useState(false);
  const [pdfStatus, setPdfStatus] = useState<"idle" | "loading" | "success" | "error">("idle");
  const [pdfError, setPdfError] = useState("");

  const loadPosts = useCallback(async (jobId: string) => {
    try {
      const items = await api.getBrandAnalysisPosts(jobId);
      setPosts(items);
    } catch (caught) {
      if (!(caught instanceof ApiError) || caught.status !== 404) {
        setError(caught instanceof Error ? caught.message : "Unable to load posts.");
      }
    }
  }, []);

  const loadReport = useCallback(async (jobId: string) => {
    try {
      const data = await api.getBrandAnalysisReport(jobId);
      setReport(data);
    } catch (caught) {
      if (!(caught instanceof ApiError) || caught.status !== 404) {
        setError(caught instanceof Error ? caught.message : "Unable to load report.");
      }
    }
  }, []);

  const loadJob = useCallback(async () => {
    const currentJob = job;
    if (!currentJob?.id) return;
    try {
      const updated = await api.getBrandAnalysisJob(currentJob.id);
      setJob(updated);
      if (updated.state === "succeeded" || updated.state === "analyzed") {
        await loadPosts(updated.id);
        await loadReport(updated.id);
      }
    } catch (caught) {
      if (!(caught instanceof ApiError) || caught.status !== 404) {
        setError(caught instanceof Error ? caught.message : "Unable to refresh job.");
      }
    }
  }, [job, loadPosts, loadReport]);

  const handleCopyReport = async () => {
    if (!report?.markdown_text) return;
    try {
      await navigator.clipboard.writeText(report.markdown_text);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1500);
    } catch {
      setError("Unable to copy report.");
    }
  };

  const handleExportPdf = useCallback(async () => {
    if (!job?.id || !report) return;
    setPdfStatus("loading");
    setPdfError("");
    try {
      const blob = await api.exportBrandAnalysisPdf(job.id);
      const objectUrl = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = objectUrl;
      link.download = `${job.target_username || report.job_id}_marka_analizi.pdf`;
      document.body.appendChild(link);
      link.click();
      document.body.removeChild(link);
      URL.revokeObjectURL(objectUrl);
      setPdfStatus("success");
      window.setTimeout(() => setPdfStatus("idle"), 2000);
    } catch (caught) {
      const message = caught instanceof Error ? caught.message : "Unable to export PDF.";
      setPdfError(message);
      setPdfStatus("error");
    }
  }, [job, report]);

  const handleStop = useCallback(async () => {
    if (!job?.id) return;
    setStopping(true);
    setError("");
    try {
      await api.stopJob(job.id);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Unable to stop analysis.");
    } finally {
      setStopping(false);
    }
  }, [job]);

  usePolling(loadJob, isRunActive(job));

  function validateInput(): string | null {
    const value = url.trim();
    if (!value) return "Enter an Instagram username or URL.";
    const username = value
      .replace(/^https?:\/\/([\w.]+\/)?instagram\.com\//, "")
      .replace(/^@/, "")
      .replace(/\/$/, "");
    if (!username || !/^[\w.]+$/.test(username)) {
      return "Enter a valid Instagram username or profile URL.";
    }
    if (maxPosts < 1 || maxPosts > 30) {
      return "Max posts must be between 1 and 30.";
    }
    return null;
  }

  const handleSubmit = async (event: FormEvent) => {
    event.preventDefault();
    const validationError = validateInput();
    if (validationError) {
      setError(validationError);
      return;
    }
    setLoading(true);
    setError("");
    setPosts([]);
    setJob(null);
    try {
      const next = await api.startBrandAnalysis({
        username_or_url: url.trim(),
        max_posts: maxPosts,
      });
      setJob(next);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Unable to start analysis.");
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="space-y-6">
      <section className="card">
        <div className="card-header">
          <h1 className="section-title">Marka referans analizi</h1>
          <p className="mt-1 text-sm text-slate-500">
            Bir Instagram hesabının son gönderilerini topla ve analiz et.
          </p>
        </div>
        <form onSubmit={handleSubmit} className="p-5 sm:p-6">
          <div className="grid gap-4 sm:grid-cols-[1fr_auto] sm:items-end">
            <div>
              <label htmlFor="brand-url" className="label">
                Instagram kullanıcı adı veya URL
              </label>
              <input
                id="brand-url"
                type="text"
                className="input w-full"
                placeholder="https://www.instagram.com/markaadi/"
                value={url}
                onChange={(e) => setUrl(e.target.value)}
                required
              />
            </div>
            <div>
              <label htmlFor="max-posts" className="label">
                Max gönderi
              </label>
              <input
                id="max-posts"
                type="number"
                min={1}
                max={30}
                className="input w-28"
                value={maxPosts}
                onChange={(e) => setMaxPosts(Number(e.target.value))}
              />
            </div>
          </div>
          <div className="mt-4 flex items-center gap-3">
            <button type="submit" className="button button-primary" disabled={loading}>
              {loading ? "Başlatılıyor..." : "Analiz et"}
            </button>
            {job && <StatusBadge state={job.state} />}
          </div>
          {error && <p className="mt-3 text-sm text-red-600">{error}</p>}
        </form>
      </section>

      {job && (
        <BrandAnalysisProgress
          job={job}
          isStopping={stopping}
          onStop={() => void handleStop()}
        />
      )}

      {job && (
        <ScraperLogConsole
          taskId={job.id}
          title="Brand analysis log"
          path={BRAND_ANALYSIS_LOGS_PATH}
          idleMessage="Bir analiz başlatın; canlı log akacak."
        />
      )}

      {posts.length > 0 && (
        <section className="card">
          <div className="card-header">
            <h2 className="section-title">Toplanan gönderiler ({posts.length})</h2>
          </div>
          <ul className="divide-y divide-slate-100">
            {posts.map((post) => (
              <li key={post.post_id} className="p-4 sm:p-5">
                <div className="flex items-start gap-4">
                  {post.media_url && (
                    <Image
                      src={post.media_url}
                      alt=""
                      width={80}
                      height={80}
                      unoptimized
                      className="h-20 w-20 rounded-md object-cover"
                    />
                  )}
                  <div className="min-w-0 flex-1">
                    <p className="text-sm font-medium text-slate-900">
                      {post.media_type} · {post.shortcode}
                    </p>
                    <p className="mt-1 line-clamp-2 text-sm text-slate-600">
                      {post.caption || "Altyazı yok"}
                    </p>
                    <p className="mt-2 text-xs text-slate-400">
                      Beğeni: {post.like_count} · Yorum: {post.comment_count}
                    </p>
                  </div>
                </div>
              </li>
            ))}
          </ul>
        </section>
      )}

      <BrandAnalysisReport
        report={report}
        copied={copied}
        onCopy={handleCopyReport}
        onExportPdf={handleExportPdf}
        pdfStatus={pdfStatus}
        pdfError={pdfError}
      />
    </div>
  );
}
