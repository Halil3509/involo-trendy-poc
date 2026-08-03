"use client";

import { useId, useState } from "react";

import { api } from "@/lib/api";
import type {
  ContentRecommendation,
  RecommendationContentFormat,
  RecommendationExperiment,
  RecommendationPostLink,
  RecommendationState,
} from "@/lib/types";

const FORMAT_LABELS: Record<RecommendationContentFormat, string> = {
  reels: "Reels",
  carousel: "Carousel",
  native_photo: "Native photo",
};

export function RecommendationCard({
  recommendation,
  onChange,
}: {
  recommendation: ContentRecommendation;
  onChange: (recommendation: ContentRecommendation) => void;
}) {
  const shootId = useId();
  const evidenceId = useId();
  const linkId = useId();
  const [shootOpen, setShootOpen] = useState(false);
  const [evidenceOpen, setEvidenceOpen] = useState(false);
  const [linkOpen, setLinkOpen] = useState(false);
  const [mediaId, setMediaId] = useState("");
  const [postLink, setPostLink] = useState<RecommendationPostLink | null>(null);
  const [experiment, setExperiment] = useState<RecommendationExperiment | null>(null);
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const [announcement, setAnnouncement] = useState("");
  const state: RecommendationState | "new" = recommendation.state ?? "new";

  async function transition(nextState: RecommendationState) {
    if (busy) return;
    const previous = recommendation;
    onChange({ ...recommendation, state: nextState });
    setBusy(nextState);
    setError("");
    try {
      const updated = await api.createRecommendationEvent(
        recommendation.id,
        {
          state: nextState,
          ...(nextState === "dismissed" ? { reason: "not_selected" } : {}),
          idempotency_key: crypto.randomUUID(),
        },
      );
      onChange({ ...recommendation, state: updated.state });
      setAnnouncement(`${recommendation.title} moved to ${nextState.replaceAll("_", " ")}.`);
    } catch (caught) {
      onChange(previous);
      setError(caught instanceof Error ? caught.message : "Unable to update this idea.");
    } finally {
      setBusy("");
    }
  }

  async function openLinker() {
    setLinkOpen(true);
  }

  async function linkPost() {
    if (!mediaId || busy) return;
    setBusy("link");
    setError("");
    try {
      const postLink = await api.linkRecommendationPost(
        recommendation.id,
        mediaId,
      );
      setPostLink(postLink);
      await api.createRecommendationEvent(recommendation.id, {
        state: "published",
        idempotency_key: crypto.randomUUID(),
      });
      onChange({ ...recommendation, state: "published" });
      setLinkOpen(false);
      setAnnouncement(`${recommendation.title} linked to a published post.`);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Unable to link post.");
    } finally {
      setBusy("");
    }
  }

  async function createExperiment() {
    if (busy) return;
    setBusy("experiment");
    setError("");
    try {
      const variants =
        recommendation.ab_hooks && recommendation.ab_hooks.length === 2
          ? recommendation.ab_hooks
          : [recommendation.hook, recommendation.hook_0_3s ?? recommendation.cta];
      setExperiment(
        await api.createRecommendationExperiment({
          recommendation_id: recommendation.id,
          name: `${recommendation.title} hook test`,
          variants,
        }),
      );
      setAnnouncement(`Experiment created for ${recommendation.title}.`);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Unable to create experiment.");
    } finally {
      setBusy("");
    }
  }

  return (
    <article className="rounded-xl border border-slate-200 bg-white p-5 shadow-sm">
      <div className="flex items-start justify-between gap-3">
        <div>
          <span className="status status-active">{FORMAT_LABELS[recommendation.content_format]}</span>
          <h4 className="mt-2 font-semibold leading-6 text-slate-900">{recommendation.title}</h4>
        </div>
        <span className="status status-success">{state.replaceAll("_", " ")}</span>
      </div>

      {(recommendation.objective || recommendation.target_audience) && (
        <dl className="mt-4 grid gap-2 rounded-lg bg-slate-50 p-3 text-sm">
          {recommendation.objective && <Detail label="Objective" value={recommendation.objective} />}
          {recommendation.target_audience && <Detail label="Audience" value={recommendation.target_audience} />}
        </dl>
      )}

      <dl className="mt-5 space-y-4 text-sm">
        <Detail label="Hook" value={recommendation.hook} />
        <Detail label="Call to action" value={recommendation.cta} />
        <Detail label="Why it fits" value={recommendation.reasoning} />
      </dl>

      {recommendation.script_beats?.length ? (
        <div className="mt-5">
          <button className="text-sm font-semibold text-indigo-700" type="button" aria-expanded={shootOpen} aria-controls={shootId} onClick={() => setShootOpen((open) => !open)}>
            {shootOpen ? "Hide shoot plan" : "View shoot plan"}
          </button>
          {shootOpen && (
            <div className="mt-3 space-y-4 rounded-xl border border-slate-200 p-4 text-sm" id={shootId}>
              <Detail label="First frame" value={recommendation.first_frame ?? recommendation.hook} />
              <Detail label="0–3 second hook" value={recommendation.hook_0_3s ?? recommendation.hook} />
              <ol className="space-y-3">
                {recommendation.script_beats.map((beat) => (
                  <li key={`${beat.at_seconds}-${beat.direction}`}>
                    <p className="font-semibold text-slate-800">{beat.at_seconds}s · {beat.direction}</p>
                    {beat.dialogue && <p className="mt-1 text-slate-600">{beat.dialogue}</p>}
                  </li>
                ))}
              </ol>
              {recommendation.shot_list?.length ? (
                <div>
                  <p className="text-xs font-bold uppercase tracking-wider text-slate-400">Shot list</p>
                  <ul className="mt-2 list-inside list-disc text-slate-700">
                    {recommendation.shot_list.map((shot) => <li key={shot.order}>{shot.framing}: {shot.action} · {shot.duration_seconds}s</li>)}
                  </ul>
                </div>
              ) : null}
              <p className="text-slate-600">{recommendation.duration_seconds ?? 30}s{recommendation.location ? ` · ${recommendation.location}` : ""}</p>
              {recommendation.caption && <Detail label="Caption draft" value={recommendation.caption} />}
              {recommendation.hashtags?.length ? <p className="text-indigo-700">{recommendation.hashtags.join(" ")}</p> : null}
            </div>
          )}
        </div>
      ) : null}

      {recommendation.evidence?.length ? (
        <div className="mt-4">
          <button className="text-sm font-semibold text-indigo-700" type="button" aria-expanded={evidenceOpen} aria-controls={evidenceId} onClick={() => setEvidenceOpen((open) => !open)}>
            {evidenceOpen ? "Hide evidence" : `Why this idea? (${recommendation.evidence.length})`}
          </button>
          {evidenceOpen && (
            <ul className="mt-3 space-y-3" id={evidenceId}>
              {recommendation.evidence.map((evidence) => (
                <li key={evidence.trend_id} className="rounded-lg border border-slate-200 p-3 text-sm">
                  <div className="flex flex-wrap gap-2">
                    <span className="status status-active">{evidence.lifecycle}</span>
                    <span className="text-xs font-semibold text-slate-500">{Math.round(evidence.confidence * 100)}% evidence confidence</span>
                    <span className="text-xs font-semibold text-slate-500">{Math.round(evidence.similarity * 100)}% similarity</span>
                  </div>
                  {evidence.snapshot_at && <time className="mt-2 block text-xs text-slate-500" dateTime={evidence.snapshot_at}>Snapshot {new Date(evidence.snapshot_at).toLocaleString()}</time>}
                  {evidence.permalink && <a className="mt-2 inline-block text-sm font-semibold text-indigo-700 underline" href={evidence.permalink} target="_blank" rel="noreferrer">
                    View Instagram evidence (opens externally)
                  </a>}
                </li>
              ))}
            </ul>
          )}
        </div>
      ) : null}

      {experiment && (
        <div className="mt-5 rounded-lg bg-violet-50 p-3 text-sm">
          <p className="font-semibold text-violet-900">Experiment · {experiment.state.replaceAll("_", " ")}</p>
          <p className="mt-1 text-violet-800">{experiment.name}</p>
          <p className="mt-1 text-xs text-violet-700">{experiment.variants.length} variants. Results depend on the available sample.</p>
        </div>
      )}

      {postLink && (
        <p className="mt-4 text-sm text-emerald-700">
          Linked to {postLink.permalink ? <a className="font-semibold underline" href={postLink.permalink} target="_blank" rel="noreferrer">published post (opens externally)</a> : <span className="font-semibold">{postLink.media_id}</span>}
        </p>
      )}

      {error && <div className="alert alert-error mt-4" role="alert">{error}</div>}
      <p className="sr-only" role="status" aria-live="polite">{announcement}</p>

      <div className="mt-5 flex flex-wrap gap-2 border-t border-slate-100 pt-4">
        {state === "new" && <>
          <Action label={`Save “${recommendation.title}”`} text="Save" disabled={Boolean(busy)} onClick={() => void transition("saved")} />
          <Action label={`Dismiss “${recommendation.title}”`} text="Dismiss" disabled={Boolean(busy)} onClick={() => void transition("dismissed")} />
        </>}
        {state === "saved" && <Action label={`Move “${recommendation.title}” to production`} text="Start production" disabled={Boolean(busy)} onClick={() => void transition("in_production")} />}
        {state === "in_production" && <Action label={`Link a published post to “${recommendation.title}”`} text="Link published post" disabled={Boolean(busy)} onClick={() => void openLinker()} />}
        {postLink && !experiment && <Action label={`Create an experiment for “${recommendation.title}”`} text="Create hook experiment" disabled={Boolean(busy)} onClick={() => void createExperiment()} />}
      </div>

      {linkOpen && (
        <div className="mt-4 rounded-xl border border-indigo-200 bg-indigo-50 p-4" id={linkId}>
          <label className="label" htmlFor={`${linkId}-post`}>Your published Instagram media ID</label>
          <input id={`${linkId}-post`} className="input" value={mediaId} placeholder="1789…" onChange={(event) => setMediaId(event.target.value)} />
          <p className="field-hint">Only media belonging to your connected account can be linked.</p>
          <div className="mt-3 flex gap-2">
            <button className="button button-primary" type="button" disabled={!mediaId || Boolean(busy)} onClick={() => void linkPost()}>{busy === "link" ? "Linking..." : "Link post"}</button>
            <button className="button button-secondary" type="button" onClick={() => setLinkOpen(false)}>Cancel</button>
          </div>
        </div>
      )}
    </article>
  );
}

function Detail({ label, value }: { label: string; value: string }) {
  return <div><dt className="text-xs font-bold uppercase tracking-wider text-slate-400">{label}</dt><dd className="mt-1 leading-6 text-slate-700">{value}</dd></div>;
}

function Action(props: { label: string; text: string; disabled: boolean; onClick: () => void }) {
  return <button className="button button-secondary" type="button" aria-label={props.label} disabled={props.disabled} onClick={props.onClick}>{props.text}</button>;
}
