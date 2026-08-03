import type {
  ContentRecommendation,
  InstagramStatus,
  Job,
  RecommendationBatch,
  RecommendationEvidence,
  RecommendationState,
  ScraperLogEvent,
  User,
} from "@/lib/types";

export function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

export function asString(value: unknown, fallback = ""): string {
  if (typeof value === "string") return value;
  if (value == null) return fallback;
  return String(value);
}

export function asStringArray(value: unknown): string[] {
  if (Array.isArray(value)) return value.map((item) => asString(item));
  if (typeof value === "string") return value ? [value] : [];
  return [];
}

export function asNumber(value: unknown, fallback = 0): number {
  if (typeof value === "number" && Number.isFinite(value)) return value;
  if (typeof value === "string") {
    const parsed = Number(value);
    if (Number.isFinite(parsed)) return parsed;
  }
  return fallback;
}

export function asBoolean(value: unknown, fallback = false): boolean {
  if (typeof value === "boolean") return value;
  if (value === "true") return true;
  if (value === "false") return false;
  return fallback;
}

export function asOptionalString(
  value: unknown,
  nullable: true,
): string | null | undefined;
export function asOptionalString(
  value: unknown,
  nullable?: false,
): string | undefined;
export function asOptionalString(
  value: unknown,
  nullable = false,
): string | null | undefined {
  if (value === undefined) return undefined;
  if (value === null) return nullable ? null : undefined;
  return asString(value);
}

export function asOptionalNumber(
  value: unknown,
  nullable: true,
): number | null | undefined;
export function asOptionalNumber(
  value: unknown,
  nullable?: false,
): number | undefined;
export function asOptionalNumber(
  value: unknown,
  nullable = false,
): number | null | undefined {
  if (value === undefined) return undefined;
  if (value === null) return nullable ? null : undefined;
  return asNumber(value);
}

export function asOptionalBoolean(value: unknown): boolean | undefined {
  if (value === undefined) return undefined;
  return asBoolean(value);
}

export function normalizeUser(value: unknown): User {
  const record = isRecord(value) ? value : {};
  return {
    id: asString(record.id, "unknown"),
    email: asString(record.email, ""),
    role: asString(record.role, "user") as User["role"],
    created_at: asString(record.created_at, ""),
  };
}

export function normalizeInstagramStatus(value: unknown): InstagramStatus {
  const record = isRecord(value) ? value : {};
  return {
    status: asString(record.status, "disconnected") as InstagramStatus["status"],
    instagram_username: asOptionalString(record.instagram_username, true),
    connected_at: asOptionalString(record.connected_at, true),
    last_synced_at: asOptionalString(record.last_synced_at, true),
    content_count_analyzed: asOptionalNumber(record.content_count_analyzed),
    ai_profile_summary: asOptionalString(record.ai_profile_summary, true),
    vector_std_dev: asOptionalNumber(record.vector_std_dev, true),
    follower_count: asOptionalNumber(record.follower_count, true),
    sync_job_id: asOptionalString(record.sync_job_id, true),
    profile_version: asOptionalNumber(record.profile_version, true),
    analytics_available: asOptionalBoolean(record.analytics_available),
    error: asOptionalString(record.error, true),
  };
}

export function normalizeJob(value: unknown): Job {
  const record = isRecord(value) ? value : {};
  return {
    id: asString(record.id, ""),
    kind: asString(record.kind, ""),
    state: asString(record.state, "queued"),
    counters: isRecord(record.counters)
      ? Object.fromEntries(
          Object.entries(record.counters).map(([key, val]) => [key, asNumber(val, 0)]),
        )
      : {},
    error: asOptionalString(record.error, true),
    created_at: asOptionalString(record.created_at),
    started_at: asOptionalString(record.started_at, true),
    finished_at: asOptionalString(record.finished_at, true),
    logs: Array.isArray(record.logs)
      ? record.logs.filter(isRecord).map(
          (log): ScraperLogEvent => ({
            ts: asString(log.ts, ""),
            level: asString(log.level, "info") as ScraperLogEvent["level"],
            message: asString(log.message, ""),
            step: asOptionalString(log.step),
            terminal: asOptionalBoolean(log.terminal),
            data: isRecord(log.data) ? log.data : undefined,
          }),
        )
      : undefined,
    target_username: asOptionalString(record.target_username, true),
    requested_url: asOptionalString(record.requested_url, true),
  };
}

export function normalizeContentRecommendation(
  item: Record<string, unknown>,
): ContentRecommendation {
  return {
    id: asString(item.id, ""),
    title: asString(item.title, ""),
    hook: asString(item.hook, ""),
    cta: asString(item.cta, ""),
    content_format: asString(item.content_format, "reels") as ContentRecommendation["content_format"],
    reasoning: asString(item.reasoning, ""),
    objective: asOptionalString(item.objective),
    target_audience: asOptionalString(item.target_audience),
    first_frame: asOptionalString(item.first_frame),
    hook_0_3s: asOptionalString(item.hook_0_3s),
    script_beats: Array.isArray(item.script_beats)
      ? item.script_beats.filter(isRecord).map((beat) => ({
          at_seconds: asNumber(beat.at_seconds),
          direction: asString(beat.direction, ""),
          dialogue: asOptionalString(beat.dialogue, true) ?? null,
        }))
      : undefined,
    shot_list: Array.isArray(item.shot_list)
      ? item.shot_list.filter(isRecord).map((shot) => ({
          order: asNumber(shot.order),
          framing: asString(shot.framing, ""),
          action: asString(shot.action, ""),
          duration_seconds: asNumber(shot.duration_seconds),
        }))
      : undefined,
    overlay_text: asOptionalString(item.overlay_text) ? asStringArray(item.overlay_text) : undefined,
    duration_seconds: asOptionalNumber(item.duration_seconds),
    location: asOptionalString(item.location, true),
    props: asOptionalString(item.props) ? asStringArray(item.props) : undefined,
    audio_direction: asOptionalString(item.audio_direction),
    caption: asOptionalString(item.caption),
    hashtags: asOptionalString(item.hashtags) ? asStringArray(item.hashtags) : undefined,
    ab_hooks: asOptionalString(item.ab_hooks) ? asStringArray(item.ab_hooks) : undefined,
    publish_window: asOptionalString(item.publish_window, true),
    why_now: asOptionalString(item.why_now),
    originality_guardrail: asOptionalString(item.originality_guardrail),
    evidence_ids: asOptionalString(item.evidence_ids) ? asStringArray(item.evidence_ids) : undefined,
    evidence: Array.isArray(item.evidence)
      ? (item.evidence as RecommendationEvidence[])
      : undefined,
    state: asOptionalString(item.state) as RecommendationState | undefined,
  };
}

export function normalizeRecommendationBatch(value: unknown): RecommendationBatch {
  const record = isRecord(value) ? value : {};
  return {
    id: asString(record.id, ""),
    created_at: asString(record.created_at, ""),
    recommendations: Array.isArray(record.recommendations)
      ? record.recommendations.filter(isRecord).map(normalizeContentRecommendation)
      : [],
  };
}

export function normalizeArray<T>(
  value: unknown,
  normalize: (item: unknown) => T,
): T[] {
  if (!Array.isArray(value)) return [];
  return value.map(normalize);
}
