"use client";

import { useCallback, useEffect, useState } from "react";

import { api } from "@/lib/api";
import { usePolling } from "@/lib/hooks";
import type { InstagramStatus, Job } from "@/lib/types";
import { isRunActive } from "@/lib/validation";

const STATUS_LABELS: Record<InstagramStatus["status"], string> = {
  disconnected: "Not connected",
  connected: "Connected",
  profiling: "Analyzing",
  ready: "Profile ready",
  failed: "Analysis failed",
  needs_reauth: "Reconnect required",
};

type InstagramProfileCardProps = {
  status: InstagramStatus | null;
  loading: boolean;
  statusError?: string;
  refreshStatus: () => Promise<InstagramStatus>;
};

export function InstagramProfileCard({
  status,
  loading,
  statusError = "",
  refreshStatus,
}: InstagramProfileCardProps) {
  const [syncJob, setSyncJob] = useState<Job | null>(null);
  const [busy, setBusy] = useState<"connect" | "disconnect" | "sync" | null>(
    null,
  );
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const [oauthWindow, setOauthWindow] = useState<Window | null>(null);

  useEffect(() => {
    const timer = window.setTimeout(() => {
      const params = new URLSearchParams(window.location.search);
      const callbackState =
        params.get("instagram") ?? params.get("instagram_status");
      const callbackError =
        params.get("instagram_error") ??
        (callbackState === "error"
          ? (params.get("message") ?? params.get("error"))
          : null);
      if (callbackError) setError(callbackError);
      else if (
        callbackState === "connected" ||
        callbackState === "success" ||
        params.get("instagram_connected") === "true"
      ) {
        setMessage("Instagram account connected successfully.");
      }
    }, 0);
    return () => window.clearTimeout(timer);
  }, []);

  const pollStatus = useCallback(async () => {
    const nextStatus = await refreshStatus();
    if (
      nextStatus.status === "ready" ||
      nextStatus.status === "failed" ||
      nextStatus.status === "needs_reauth"
    ) {
      setSyncJob(null);
    }
  }, [refreshStatus]);

  usePolling(
    pollStatus,
    status?.status === "profiling" || isRunActive(syncJob),
    { onError: (err) => setError(err.message) },
  );

  useEffect(() => {
    if (!oauthWindow) return;

    const timer = window.setInterval(() => {
      if (oauthWindow.closed) {
        window.clearInterval(timer);
        setOauthWindow(null);
        setBusy(null);
        void refreshStatus();
      }
    }, 800);

    return () => window.clearInterval(timer);
  }, [oauthWindow, refreshStatus]);

  useEffect(() => {
    function onMessage(event: MessageEvent) {
      if (event.origin !== window.location.origin) return;

      const data = event.data;
      if (
        typeof data !== "object" ||
        data === null ||
        data.type !== "involo:instagram:oauth"
      ) {
        return;
      }

      if (oauthWindow && !oauthWindow.closed) {
        oauthWindow.close();
      }
      setOauthWindow(null);
      setBusy(null);

      if (data.status === "connected" || data.status === "success") {
        setMessage(
          typeof data.message === "string" && data.message
            ? data.message
            : "Instagram account connected successfully.",
        );
      } else {
        setError(
          typeof data.message === "string" && data.message
            ? data.message
            : "Instagram connection failed.",
        );
      }
      void refreshStatus();
    }

    window.addEventListener("message", onMessage);
    return () => window.removeEventListener("message", onMessage);
  }, [oauthWindow, refreshStatus]);

  async function connect() {
    setBusy("connect");
    setError("");
    try {
      const { authorization_url } = await api.startInstagramOAuth();
      const width = 520;
      const height = 700;
      const left = Math.max(
        0,
        window.screenX + Math.round((window.outerWidth - width) / 2),
      );
      const top = Math.max(
        0,
        window.screenY + Math.round((window.outerHeight - height) / 2),
      );
      const features = [
        "popup",
        `width=${width}`,
        `height=${height}`,
        `left=${left}`,
        `top=${top}`,
        "resizable",
        "scrollbars",
      ].join(",");
      const popup = window.open(
        authorization_url,
        "instagram_oauth",
        features,
      );
      if (!popup) {
        throw new Error(
          "Please allow popups for this site to connect Instagram.",
        );
      }
      setOauthWindow(popup);
    } catch (caught) {
      setError(
        caught instanceof Error ? caught.message : "Unable to start connection.",
      );
      setBusy(null);
      setOauthWindow(null);
    }
  }

  async function disconnect() {
    if (!window.confirm("Disconnect this Instagram account?")) return;
    setBusy("disconnect");
    setError("");
    setMessage("");
    try {
      await api.disconnectInstagram();
      setSyncJob(null);
      await refreshStatus();
      setMessage("Instagram account disconnected.");
    } catch (caught) {
      setError(
        caught instanceof Error ? caught.message : "Unable to disconnect account.",
      );
    } finally {
      setBusy(null);
    }
  }

  async function sync() {
    setBusy("sync");
    setError("");
    setMessage("");
    try {
      const job = await api.syncProfile();
      setSyncJob(job);
      await refreshStatus();
      setMessage("Profile analysis queued.");
    } catch (caught) {
      setError(
        caught instanceof Error ? caught.message : "Unable to start analysis.",
      );
    } finally {
      setBusy(null);
    }
  }

  if (loading) {
    return (
      <section className="card p-8" aria-label="Instagram profile">
        <div className="flex items-center gap-3" role="status">
          <span className="spinner" aria-hidden="true" />
          <span className="text-sm text-slate-500">Loading Instagram profile...</span>
        </div>
      </section>
    );
  }

  const name = status?.instagram_username;
  const connected = status && status.status !== "disconnected";
  const canSync =
    status?.status === "connected" ||
    status?.status === "ready" ||
    (status?.status === "failed" && Boolean(name));
  const needsConnection =
    !status || status.status === "disconnected" || status.status === "needs_reauth";

  return (
    <section className="card" aria-labelledby="instagram-heading">
      <div className="card-header flex flex-wrap items-start justify-between gap-3">
        <div>
          <p className="eyebrow">Personalization</p>
          <h2 id="instagram-heading" className="section-title mt-1">
            Instagram profile
          </h2>
        </div>
        {status && (
          <span
            className={`status ${
              status.status === "ready"
                ? "status-success"
                : status.status === "failed" ||
                    status.status === "needs_reauth"
                  ? "status-danger"
                  : "status-active"
            }`}
          >
            {STATUS_LABELS[status.status]}
          </span>
        )}
      </div>

      <div className="space-y-5 p-5 sm:p-6">
        {(error || statusError) && (
          <div className="alert alert-error" role="alert">
            {error || statusError}
          </div>
        )}
        {message && (
          <div className="alert alert-success" role="status">
            {message}
          </div>
        )}

        {needsConnection ? (
          <div>
            <p className="text-sm leading-6 text-slate-600">
              {status?.status === "needs_reauth"
                ? "Your Instagram authorization expired. Reconnect to continue profile analysis."
                : "Connect Instagram to build an AI profile from your content and improve recommendations."}
            </p>
            <button
              type="button"
              className="button button-primary mt-4"
              disabled={busy !== null}
              onClick={() => void connect()}
            >
              {busy === "connect" && <span className="spinner spinner-light" />}
              {status?.status === "needs_reauth" ? "Reconnect Instagram" : "Connect Instagram"}
            </button>
          </div>
        ) : (
          <>
            <dl className="grid gap-3 text-sm sm:grid-cols-2 lg:grid-cols-5">
              <ProfileMetric label="Account" value={name ? `@${name}` : "Connected"} />
              <ProfileMetric
                label="Followers"
                value={status?.follower_count?.toLocaleString() ?? "—"}
              />
              <ProfileMetric
                label="Content analyzed"
                value={String(status?.content_count_analyzed ?? 0)}
              />
              <ProfileMetric
                label="Last synced"
                value={formatDate(status?.last_synced_at)}
              />
              <ProfileMetric
                label="Profile version"
                value={status?.profile_version ? `v${status.profile_version}` : "—"}
              />
            </dl>

            {status?.ai_profile_summary && (
              <div className="rounded-xl bg-indigo-50 p-4">
                <h3 className="text-sm font-semibold text-indigo-950">
                  Your content profile
                </h3>
                <p className="mt-2 whitespace-pre-wrap text-sm leading-6 text-indigo-900">
                  {status.ai_profile_summary}
                </p>
                {status.vector_std_dev != null && (
                  <p className="mt-3 text-xs font-medium text-indigo-700">
                    Content diversity score: {status.vector_std_dev.toFixed(3)}
                  </p>
                )}
              </div>
            )}

            {status?.status === "profiling" && (
              <div className="flex items-center gap-3 text-sm text-indigo-700" role="status">
                <span className="spinner" aria-hidden="true" />
                Analyzing your content. This page updates automatically.
              </div>
            )}
            {status?.error && (
              <div className="alert alert-error" role="alert">
                {status.error}
              </div>
            )}

            <div className="flex flex-wrap gap-2 border-t border-slate-100 pt-5">
              {canSync && (
                <button
                  type="button"
                  className="button button-primary"
                  disabled={busy !== null || isRunActive(syncJob)}
                  onClick={() => void sync()}
                >
                  {(busy === "sync" || isRunActive(syncJob)) && (
                    <span className="spinner spinner-light" />
                  )}
                  {isRunActive(syncJob) ? "Analysis queued" : "Analyze now"}
                </button>
              )}
              {connected && (
                <button
                  type="button"
                  className="button button-secondary"
                  disabled={busy !== null}
                  onClick={() => void disconnect()}
                >
                  {busy === "disconnect" && <span className="spinner" />}
                  Disconnect
                </button>
              )}
            </div>
          </>
        )}
      </div>
    </section>
  );
}

function ProfileMetric({ label, value }: { label: string; value: string }) {
  return (
    <div className="metric">
      <dt>{label}</dt>
      <dd className="text-base! tracking-normal!">{value}</dd>
    </div>
  );
}

function formatDate(value?: string | null): string {
  return value ? new Date(value).toLocaleString() : "Not yet";
}
