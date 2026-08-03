import type {
  AdminObservability,
  AdminOverview,
  InstagramStatus,
  Job,
  PipelineStats,
  ProfileAnalytics,
  ProfilingConfig,
  ProfilingEstimate,
  RecommendationBatch,
  ScraperConfig,
  TrendContent,
  TrendContentListResponse,
  User,
} from "../../src/lib/types";

export const adminUser: User = {
  id: "e2e-admin-1",
  email: "admin@e2e.local",
  role: "admin",
  created_at: "2026-07-17T00:00:00Z",
};

export const regularUser: User = {
  id: "e2e-user-1",
  email: "creator@e2e.local",
  role: "user",
  created_at: "2026-07-17T00:00:00Z",
};

export const preferences = {
  target_countries: ["TR"],
  target_cities: ["Istanbul"],
  content_languages: ["tr"],
  timezone: "Europe/Istanbul",
  niches: ["travel", "food"],
  goals: ["saves"],
  constraints: ["indoor only"],
  updated_at: "2026-07-17T00:00:00Z",
};

export const scraperConfig: ScraperConfig = {
  keywords: ["fashion"],
  reels_per_keyword: 12,
  headless: true,
  viral_threshold: 20,
  transcribe_min_views: 0,
  schedule_cron: "0 5 * * *",
  schedule_pipeline: false,
};

export const latestScraperRun: Job = {
  id: "scrape-e2e-1",
  kind: "scrape",
  state: "succeeded",
  counters: { discovered: 18, inserted: 15 },
  created_at: "2026-07-17T09:00:00Z",
  started_at: "2026-07-17T09:00:01Z",
  finished_at: "2026-07-17T09:01:00Z",
};

export const pipelineStats: PipelineStats = {
  discovered: 5,
  enriched: 3,
  stored: 1,
  embedded: 2,
  needs_intervention: 0,
  failed: 0,
};

export const latestPipelineRun: Job = {
  id: "pipeline-e2e-1",
  kind: "enrich",
  state: "succeeded",
  counters: { processed: 3, scored: 3 },
  created_at: "2026-07-17T09:05:00Z",
  started_at: "2026-07-17T09:05:01Z",
  finished_at: "2026-07-17T09:06:00Z",
};

export const profilingConfig: ProfilingConfig = {
  enabled: true,
  schedule_cron: "0 3 * * *",
};

export const profilingEstimate: ProfilingEstimate = {
  connected_users: 12,
  average_seconds_per_user: 5,
  estimated_duration_seconds: 60,
  estimated_start_at: "2026-07-17T03:00:00Z",
  estimated_finish_at: "2026-07-17T03:01:00Z",
};

export const latestProfilingRun: Job = {
  id: "profiling-e2e-1",
  kind: "profile",
  state: "succeeded",
  counters: { profiled: 11, failed: 1 },
  created_at: "2026-07-17T03:00:00Z",
  started_at: "2026-07-17T03:00:01Z",
  finished_at: "2026-07-17T03:01:00Z",
};

export const adminOverview: AdminOverview = {
  total_users: 7,
  admin_users: 2,
  connected_instagram: 4,
  needs_reauth: 1,
  trend_content_total: 30,
  pipeline: pipelineStats,
  user_content_total: 15,
  user_profiles_ready: 3,
  recommendation_batches: 9,
  jobs_by_state: { succeeded: 5, failed: 1 },
  attention_jobs: 1,
};

export const adminJobs: Job[] = [
  {
    id: "job-ok",
    kind: "scrape",
    state: "succeeded",
    counters: { discovered: 10 },
    created_at: "2026-07-16T10:00:00Z",
  },
  {
    id: "job-bad",
    kind: "enrich",
    state: "failed",
    counters: {},
    error: "boom",
    created_at: "2026-07-16T11:00:00Z",
  },
];

export const adminObservability: AdminObservability = {
  queue_age_seconds: 30,
  job_duration_p50_seconds: 8,
  job_duration_p95_seconds: 20,
  stale_trends: 2,
  stale_profiles: 1,
  attention_jobs: 1,
  stale_jobs: 0,
  snapshot_coverage: 0.86,
  multimodal_failures: { PROVIDER_TIMEOUT: 1 },
  provider_usage: {
    totals: {
      input_tokens: 1200,
      output_tokens: 300,
      estimated_cost: 0.42,
    },
    groups: [
      {
        provider: "bedrock",
        model_id: "nova-pro-v1",
        stage: "vision",
        runs: 8,
        failures: 1,
        media_seconds: 240,
        average_duration_ms: 1250,
      },
    ],
  },
  evaluation: {
    latest: {
      _id: "evaluation-1",
      model_version: "ranking-v2",
      data_cutoff: "2026-07-16T00:00:00Z",
      evaluation_version: "offline-ranking-v1",
      label_definition: "Later snapshot views above median",
      k: 10,
      sample_size: 20,
      candidate_sample_size: 200,
      metrics: {
        ndcg_at_k: 0.72,
        precision_at_k: 0.31,
        brier: 0.18,
        reliability_buckets: [],
        p95_latency_seconds: 12,
        cost_per_prediction: 0.04,
      },
      thresholds: {
        min_ndcg_at_k: 0.5,
        min_precision_at_k: 0.2,
        max_brier: 0.25,
        max_p95_latency_seconds: 30,
        max_cost_per_prediction: 1,
      },
      passed: true,
      rollback_recommended: false,
      created_at: "2026-07-17T18:00:00Z",
    },
    thresholds: {
      min_ndcg_at_k: 0.5,
      min_precision_at_k: 0.2,
      max_brier: 0.25,
      max_p95_latency_seconds: 30,
      max_cost_per_prediction: 1,
      rollback_ndcg_drop: 0.1,
      rollback_precision_drop: 0.1,
      rollback_brier_increase: 0.05,
    },
  },
  funnel: { generated: 21, saved: 6, published: 1 },
};

export const instagramStatus: InstagramStatus = {
  status: "ready",
  instagram_username: "fixture_creator",
  connected_at: "2026-07-15T10:00:00Z",
  last_synced_at: "2026-07-17T06:00:00Z",
  content_count_analyzed: 24,
  ai_profile_summary: "Travel-focused creator with a warm, practical tone.",
  vector_std_dev: 0.12,
  follower_count: 25000,
  sync_job_id: "job-sync-1",
  profile_version: 2,
  analytics_available: true,
};

export const profileAnalytics: ProfileAnalytics = {
  schema_version: "creator-profile-v2",
  pillars: [
    {
      id: "semantic:travel",
      name: "Travel",
      description: "Coastal towns and practical itineraries.",
      content_count: 14,
      average_performance_residual: 1.25,
      strengths: ["Authentic locations", "Clear hooks"],
      opportunities: ["Add stronger CTAs"],
      confidence: 0.92,
    },
    {
      id: "semantic:food",
      name: "Food",
      description: "Quick recipes and local flavors.",
      content_count: 10,
      average_performance_residual: 0.75,
      strengths: ["Tight pacing"],
      opportunities: ["Show plating details"],
      confidence: 0.88,
    },
  ],
  winning_patterns: ["Opening with a question", "Day-in-the-life framing"],
  losing_patterns: ["Long intros without a hook"],
  audience_markets: ["TR", "Istanbul"],
  avoid_patterns: ["Over-produced studio shots"],
  data_quality: 0.86,
};

export const recommendations: RecommendationBatch[] = [
  {
    id: "batch-e2e-1",
    created_at: "2026-07-17T08:00:00Z",
    recommendations: [
      {
        id: "rec-e2e-1",
        title: "Hidden Istanbul street food",
        hook: "This 30-second reel starts with a sizzling köfte close-up.",
        cta: "Save this spot for your next trip.",
        content_format: "reels",
        reasoning: "Matches travel and food pillars.",
      },
      {
        id: "rec-e2e-2",
        title: "Sunset ferry route",
        hook: "Skip the crowded tram — take this ferry line at golden hour.",
        cta: "Tag a friend who needs a reset.",
        content_format: "reels",
        reasoning: "Strong audience-market fit.",
      },
    ],
  },
];

export const queuedScrapeRun: Job = {
  id: "scrape-e2e-new",
  kind: "scrape",
  state: "queued",
  counters: {},
  created_at: "2026-07-17T12:00:00Z",
};

export const queuedPipelineRun: Job = {
  id: "pipeline-e2e-new",
  kind: "pipeline",
  state: "queued",
  counters: {},
  created_at: "2026-07-17T12:01:00Z",
};

export const queuedProfilingRun: Job = {
  id: "profiling-e2e-new",
  kind: "profile",
  state: "queued",
  counters: {},
  created_at: "2026-07-17T12:02:00Z",
};

export const trendContentDetail: TrendContent = {
  id: "trend-e2e-1",
  shortcode: "Fixture_A1",
  owner_username: "fixture_creator",
  caption_text: "A deterministic travel fixture caption",
  discovered_keywords: ["travel"],
  processing_status: "embedded",
  last_upsert_action: "inserted",
  last_scrape_job_id: "scrape-e2e-1",
  viral_score: 49.4,
  created_at: "2026-07-10T00:00:00Z",
  updated_at: "2026-07-17T12:05:00Z",
  canonical_url: "https://www.instagram.com/reel/Fixture_A1/",
  media_id: "3001",
  video_url: "https://example.invalid/video-a1.mp4",
  thumbnail_url: "https://example.invalid/travel.jpg",
  source: "instagram",
  metrics: {
    view_count: 500000,
    like_count: 12000,
    comment_count: 340,
    share_count: 800,
    owner_follower_count: 25000,
  },
  score_components: {
    distribution_score: 0.9999546000702375,
    engagement_score: 0.24391388457693042,
    velocity_score: 0.6934111492002782,
    weighted_engagement_rate: 0.0466,
    raw_score: 0.6811793501612576,
  },
  transcript: "Welcome to another travel reel exploring hidden coastal towns.",
  language: "en",
  combined_text:
    "A deterministic travel fixture caption\n\nWelcome to another travel reel exploring hidden coastal towns.",
  duration_seconds: 34,
  taken_at: "2026-07-10T00:00:00Z",
  embedding_vector_id: "embedding-vector-a1",
  embedding_schema_version: "nova-mm-v2",
  enriched_at: "2026-07-17T12:03:00Z",
  embedded_at: "2026-07-17T12:05:00Z",
  first_seen_at: "2026-07-17T12:00:00Z",
  last_seen_at: "2026-07-17T12:05:00Z",
};

export const trendContentList: TrendContentListResponse = {
  items: [
    trendContentDetail,
    {
      id: "trend-e2e-2",
      shortcode: "Fixture_B2",
      owner_username: "fixture_creator",
      caption_text: "A deterministic food fixture",
      discovered_keywords: ["food"],
      processing_status: "enriched",
      last_upsert_action: "inserted",
      last_scrape_job_id: "scrape-e2e-1",
      viral_score: 15.7,
      created_at: "2026-05-01T00:00:00Z",
      updated_at: "2026-07-17T12:03:00Z",
      canonical_url: "https://www.instagram.com/reel/Fixture_B2/",
      metrics: {
        view_count: 900,
        like_count: 40,
        comment_count: 2,
        share_count: 1,
        owner_follower_count: 8000,
      },
      transcript: "Quick food snack idea you can make in five minutes.",
      language: "en",
    },
  ],
  total: 2,
  limit: 20,
  offset: 0,
};

export const defaultFixtures = {
  user: adminUser,
  preferences,
  adminOverview,
  adminJobs,
  adminObservability,
  scraperConfig,
  latestScraperRun,
  pipelineStats,
  latestPipelineRun,
  profilingConfig,
  profilingEstimate,
  latestProfilingRun,
  instagramStatus,
  profileAnalytics,
  recommendations,
  queuedScrapeRun,
  queuedPipelineRun,
  queuedProfilingRun,
  trendContentList,
  trendContentDetail,
};

export type Fixtures = typeof defaultFixtures;
