export type User = {
  id: string;
  email: string;
  role: "user" | "admin";
  created_at: string;
};

export type AuthResponse = {
  user: User;
};

export type InstagramStatusName =
  | "disconnected"
  | "connected"
  | "profiling"
  | "ready"
  | "failed"
  | "needs_reauth";

export type InstagramStatus = {
  status: InstagramStatusName;
  instagram_username?: string | null;
  connected_at?: string | null;
  last_synced_at?: string | null;
  content_count_analyzed?: number;
  ai_profile_summary?: string | null;
  vector_std_dev?: number | null;
  follower_count?: number | null;
  sync_job_id?: string | null;
  profile_version?: number | null;
  analytics_available?: boolean;
  error?: string | null;
};

export type CreatorPreferences = {
  target_countries: string[];
  target_cities: string[];
  content_languages: string[];
  timezone: string;
  niches: string[];
  goals: string[];
  constraints: string[];
  updated_at: string | null;
};

export type CreatorPreferencesUpdate = Omit<
  CreatorPreferences,
  "updated_at"
>;

export type ContentPillar = {
  id: string;
  name: string;
  description: string;
  content_count: number;
  average_performance_residual: number;
  strengths: string[];
  opportunities: string[];
  confidence: number;
};

export type ProfileAnalytics = {
  schema_version: string;
  pillars: ContentPillar[];
  winning_patterns: string[];
  losing_patterns: string[];
  audience_markets: string[];
  avoid_patterns: string[];
  data_quality: number;
};

export type RecommendationContentFormat =
  | "reels"
  | "carousel"
  | "native_photo";

export type RecommendationState =
  | "saved"
  | "dismissed"
  | "in_production"
  | "published"
  | "archived";

export type ExperimentStatus =
  | "draft"
  | "running"
  | "awaiting_data"
  | "completed"
  | "inconclusive";

export type RecommendationEvidence = {
  evidence_id: string;
  trend_id: string;
  permalink: string | null;
  similarity: number;
  lifecycle: string;
  confidence: number;
  snapshot_at: string | null;
  score_components: Record<string, number | null>;
};

export type RecommendationExperiment = {
  id: string;
  recommendation_id: string;
  name: string;
  variants: string[];
  state: ExperimentStatus;
  created_at: string;
  updated_at: string;
};

export type RecommendationPostLink = {
  id: string;
  recommendation_id: string;
  media_id: string;
  permalink: string | null;
  linked_at: string;
};

export type RecommendationEvent = {
  id: string;
  recommendation_id: string;
  state: RecommendationState;
  created_at: string;
};

export type RecommendationExperimentCreate = {
  recommendation_id: string;
  name: string;
  variants: string[];
};

export type ContentRecommendation = {
  id: string;
  title: string;
  hook: string;
  cta: string;
  content_format: RecommendationContentFormat;
  objective?: string;
  target_audience?: string;
  first_frame?: string;
  hook_0_3s?: string;
  script_beats?: Array<{
    at_seconds: number;
    direction: string;
    dialogue: string | null;
  }>;
  shot_list?: Array<{
    order: number;
    framing: string;
    action: string;
    duration_seconds: number;
  }>;
  overlay_text?: string[];
  duration_seconds?: number;
  location?: string | null;
  props?: string[];
  audio_direction?: string;
  caption?: string;
  hashtags?: string[];
  ab_hooks?: string[];
  publish_window?: string | null;
  why_now?: string;
  originality_guardrail?: string;
  evidence_ids?: string[];
  reasoning: string;
  evidence?: RecommendationEvidence[];
  state?: RecommendationState;
};

export type RecommendationBatch = {
  id: string;
  created_at: string;
  recommendations: ContentRecommendation[];
};

export type ProfilingConfig = {
  enabled: boolean;
  schedule_cron: string | null;
};

export type ProfilingEstimate = {
  connected_users: number;
  average_seconds_per_user: number;
  estimated_duration_seconds: number;
  estimated_start_at: string | null;
  estimated_finish_at: string | null;
};

export type ScraperConfig = {
  keywords: string[];
  reels_per_keyword: number;
  headless: boolean;
  enabled?: boolean;
  viral_threshold?: number;
  transcribe_min_views?: number;
  schedule_cron?: string | null;
  schedule_pipeline?: boolean;
};

export type JobKind = "scrape" | "enrich" | "embed" | "pipeline" | "brand_analysis";

export type JobState =
  | "queued"
  | "running"
  | "fetching"
  | "fetched"
  | "analyzing"
  | "reporting"
  | "analyzed"
  | "succeeded"
  | "failed"
  | "needs_intervention"
  | "skipped_locked"
  | "cancelled"
  | string;

export type BrandAnalysisRequest = {
  username_or_url: string;
  max_posts?: number;
};

export type BrandAnalysisMediaEvidence = {
  url: string | null;
  media_type: string;
  label: string;
  offset_seconds?: number | null;
  alt_text: string;
};

export type BrandAnalysisPost = {
  job_id: string;
  post_id: string;
  shortcode: string;
  permalink: string | null;
  caption: string;
  media_type: string;
  media_url: string | null;
  media_items?: BrandAnalysisMediaEvidence[];
  taken_at: string | null;
  like_count: number;
  comment_count: number;
  view_count: number;
  share_count: number;
  comments_available?: boolean;
  comment_samples?: string[];
  fetched_at: string;
};

export type ConfidenceLevel = "low" | "medium" | "high";

export type MetricObservation = {
  label: string;
  value: number;
  basis: string;
  comparable: boolean;
  confidence: ConfidenceLevel;
  note?: string;
};

export type PerformanceSummary = {
  organic_metrics: MetricObservation[];
  anomaly_metrics: MetricObservation[];
  data_quality_notes: string[];
  valid_rate_comparisons: string[];
  invalid_rate_comparisons: string[];
};

export type EvidenceReference = {
  shortcode: string;
  permalink: string | null;
  field: string;
  excerpt: string;
  why_supports: string;
  confidence: ConfidenceLevel;
};

export type EvidenceChain = {
  chain_id?: string;
  observation: string;
  semantic_meaning: string;
  preference_hypothesis: string;
  adaptable_principle: string;
  strategic_decision: string;
  evidence: EvidenceReference[];
  alternative_explanation: string;
  confidence: ConfidenceLevel;
};

export type BrandWorldSynthesis = {
  emotional_effect: string;
  brand_promise: string;
  persona: string;
  visual_codes: string[];
  verbal_codes: string[];
  lifestyle_context: string;
  premium_mechanism: string;
  avoided_elements: string[];
  confidence: ConfidenceLevel;
};

export type ContentRecipeFormatRole = {
  format: string;
  count: number;
  percentage: number;
  role_in_brand_world: string;
  content_jobs: string[];
  confidence: ConfidenceLevel;
};

export type ContentRecipe = {
  observed_window_days: number | null;
  coverage_label: string;
  cadence_estimate: string;
  posts_per_week_estimate: number | null;
  cadence_confidence: ConfidenceLevel;
  formats: ContentRecipeFormatRole[];
  content_jobs: string[][];
  anomaly_count: number;
  anomaly_note: string;
  confidence: ConfidenceLevel;
};

export type StrategicDecision = {
  decision: string;
  rationale: string;
  evidence_chain_ids: string[];
  guardrail: string;
  first_action: string;
  success_signal: string;
  confidence: ConfidenceLevel;
};

export type SuccessDNATriad = {
  desire: string;
  proof: string;
  lifestyle: string;
};

export type ContentSeriesMechanic = {
  mechanic_name: string;
  base_category_type: string;
  observed_frequency: number;
  percentage_of_sample: number;
  psychological_function: string;
  execution_formula: string;
  content_jobs: string[];
  sample_shortcodes: string[];
  evidence_excerpt: string;
  confidence: ConfidenceLevel;
};

export type VisualDNA = {
  color_palette: string[];
  lighting_recipe: string;
  texture_signatures: string[];
  shooting_angles: string[];
  aesthetic_style: string;
  avoided_visual_elements: string[];
  confidence: ConfidenceLevel;
};

export type PersonaProfile = {
  age_range: string;
  lifestyle_descriptor: string;
  aspiration: string;
  psychological_trigger: string;
  trigger_phrases: string[];
  confidence: ConfidenceLevel;
};

export type CarouselSlide = {
  slide_number: number;
  role: string;
  content_pattern: string;
};

export type CarouselAnatomy = {
  hook_pattern: string;
  slide_roles: CarouselSlide[];
  cta_pattern: string;
  avg_slide_count: number;
  confidence: ConfidenceLevel;
};

export type ContentCalendarEntry = {
  day: string;
  content_cluster: string;
  format: string;
  hook_template: string;
  slide_count: number | null;
  cta_template: string;
};

export type WeeklyContentCalendar = {
  entries: ContentCalendarEntry[];
  weekly_cadence_note: string;
  confidence: ConfidenceLevel;
};

export type BrandAnalysisStrategicBrief = {
  schema_version?: string;
  executive_answer: string;
  success_dna: SuccessDNATriad;
  brand_world: BrandWorldSynthesis;
  visual_dna?: VisualDNA | null;
  persona_profile?: PersonaProfile | null;
  carousel_anatomy?: CarouselAnatomy | null;
  weekly_content_calendar?: WeeklyContentCalendar | null;
  production_brief?: string[];
  preference_hypotheses: EvidenceChain[];
  evidence_chains: EvidenceChain[];
  content_recipe: ContentRecipe;
  content_series: ContentSeriesMechanic[];
  performance_summary: PerformanceSummary;
  limitations: string[];
  decisions: StrategicDecision[];
};

export type BrandAnalysisReport = {
  schema_version: string;
  job_id: string;
  markdown_text: string;
  report_s3_key: string | null;
  pdf_s3_key?: string | null;
  media_evidence?: BrandAnalysisMediaEvidence[];
  strategic_brief?: BrandAnalysisStrategicBrief | null;
};

export type JobProgressKeyword = {
  name: string;
  discovered: number;
  status: string;
};

export type JobProgress = {
  current_keyword: string | null;
  current_step: string;
  keywords: JobProgressKeyword[];
  total_discovered: number;
  total_target: number;
};

export type JobIntervention = {
  prompt: string;
  fields: string[];
  requested_at: string;
};

export type Job = {
  id: string;
  kind: JobKind | string;
  state: JobState;
  counters: Record<string, number>;
  progress?: JobProgress | null;
  intervention?: JobIntervention | null;
  error?: string | null;
  created_at?: string;
  started_at?: string | null;
  finished_at?: string | null;
  logs?: ScraperLogEvent[];
  target_username?: string | null;
  requested_url?: string | null;
};

export type PipelineStats = {
  discovered: number;
  enriched: number;
  stored: number;
  embedded: number;
  needs_intervention: number;
  failed: number;
};

export type AdminOverview = {
  total_users: number;
  admin_users: number;
  connected_instagram: number;
  needs_reauth: number;
  trend_content_total: number;
  pipeline: PipelineStats;
  user_content_total: number;
  user_profiles_ready: number;
  recommendation_batches: number;
  jobs_by_state: Record<string, number>;
  attention_jobs: number;
};

export type TrendContentStatus =
  | "discovered"
  | "enriched"
  | "stored"
  | "embedded"
  | "failed"
  | "needs_intervention"
  | string;

export type TrendContent = {
  id: string;
  shortcode: string | null;
  owner_username: string | null;
  caption_text: string;
  discovered_keywords: string[];
  processing_status: TrendContentStatus;
  last_upsert_action: "inserted" | "updated" | null;
  last_scrape_job_id: string | null;
  viral_score: number;
  created_at: string | null;
  updated_at: string | null;
  canonical_url?: string | null;
  media_id?: string | null;
  video_url?: string | null;
  thumbnail_url?: string | null;
  author?: string | null;
  source?: string | null;
  metrics?: Record<string, number> | null;
  score_components?: Record<string, number> | null;
  transcript?: string | null;
  language?: string | null;
  combined_text?: string | null;
  duration_seconds?: number | null;
  taken_at?: string | null;
  media_asset?: unknown;
  keyframes?: unknown[];
  visual_analysis?: unknown;
  video_segments?: unknown[];
  processing_regions?: Record<string, string> | null;
  embedding_vector_id?: string | null;
  embedding_schema_version?: string | null;
  enrichment_error?: string | null;
  embedded_at?: string | null;
  enriched_at?: string | null;
  first_seen_at?: string | null;
  last_seen_at?: string | null;
};

export type TrendContentListResponse = {
  items: TrendContent[];
  total: number;
  limit: number;
  offset: number;
};

export type TrendContentFilters = {
  status?: TrendContentStatus;
  job_id?: string;
  action?: "inserted" | "updated";
  keyword?: string;
  search?: string;
  sort?: string;
  limit?: number;
  offset?: number;
};

export type AdminObservability = {
  queue_age_seconds: number | null;
  job_duration_p50_seconds: number | null;
  job_duration_p95_seconds: number | null;
  stale_trends: number;
  stale_profiles: number;
  attention_jobs: number;
  stale_jobs: number;
  snapshot_coverage: number;
  multimodal_failures: Record<string, number>;
  provider_usage: ProviderUsage;
  evaluation: {
    latest: EvaluationRunSnapshot | null;
    thresholds: EvaluationQualityThresholds;
  };
  funnel: Record<string, number>;
};

export type ProviderUsageGroup = {
  provider: string;
  model_id: string;
  stage: string;
  runs: number;
  failures: number;
  media_seconds: number;
  average_duration_ms: number;
};

export type ProviderUsage = {
  totals: {
    input_tokens: number;
    output_tokens: number;
    estimated_cost: number;
  };
  groups: ProviderUsageGroup[];
};

export type ReliabilityBucket = {
  lower: number;
  upper: number;
  count: number;
  mean_probability: number;
  observed_rate: number;
};

export type EvaluationMetrics = {
  ndcg_at_k: number;
  precision_at_k: number;
  brier: number;
  reliability_buckets: ReliabilityBucket[];
  p95_latency_seconds: number | null;
  cost_per_prediction: number;
};

export type EvaluationRunThresholds = {
  min_ndcg_at_k: number;
  min_precision_at_k: number;
  max_brier: number;
  max_p95_latency_seconds: number;
  max_cost_per_prediction: number;
};

export type EvaluationQualityThresholds = EvaluationRunThresholds & {
  rollback_ndcg_drop: number;
  rollback_precision_drop: number;
  rollback_brier_increase: number;
};

export type EvaluationRunRequest = {
  model_version: string;
  data_cutoff: string;
  k: number;
};

export type EvaluationRun = {
  id: string;
  model_version: string;
  data_cutoff: string;
  evaluation_version: string;
  label_definition: string;
  k: number;
  sample_size: number;
  candidate_sample_size: number;
  metrics: EvaluationMetrics;
  thresholds: EvaluationRunThresholds;
  passed: boolean;
  rollback_recommended: boolean;
  created_at: string;
};

export type EvaluationRunSnapshot = Omit<EvaluationRun, "id"> & {
  _id: string;
};

export type ScraperLogEvent = {
  ts: string;
  level: "info" | "success" | "error" | string;
  message: string;
  step?: string;
  terminal?: boolean;
  data?: Record<string, unknown>;
};

export type TrackedCreatorStatus =
  | "active"
  | "tracking"
  | "needs_intervention"
  | "not_found"
  | "failed"
  | string;

export type TrackedCreator = {
  id: string;
  username: string;
  display_name: string;
  avatar_url: string | null;
  follower_count: number;
  media_count: number;
  trend_score: number;
  status: TrackedCreatorStatus;
  last_tracked_at: string | null;
  last_error: string | null;
  added_at: string | null;
};

export type TrackedCreatorDetail = TrackedCreator & {
  bio: string;
  following_count: number;
  ai_summary: string | null;
  structured_profile: Record<string, unknown> | null;
  average_viral_score: number | null;
  profile_updated_at: string | null;
};

export type FollowerPoint = {
  captured_at: string;
  follower_count: number;
};

export type FollowerHistoryRange = "week" | "month" | "year";

export type FollowerHistory = {
  range: FollowerHistoryRange;
  points: FollowerPoint[];
  delta: number;
};

export type CreatorContentItem = {
  shortcode: string;
  permalink: string | null;
  caption_text: string;
  media_type: string;
  thumbnail_url: string | null;
  taken_at: string | null;
  like_count: number;
  comment_count: number;
  view_count: number;
  viral_score: number;
  is_new: boolean;
  processing_status: string;
};

export type CreatorContentListResponse = {
  items: CreatorContentItem[];
  new_count: number;
};
