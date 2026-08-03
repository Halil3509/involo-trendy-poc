# Involo — Proje Mimarisi

Bu belge, çalışan projeye hızlı giriş rehberidir. Üretim mimarisi, veri sözlüğü,
güvenlik, model sürümleri, operasyon, hata senaryoları ve Mermaid akışlarının
ayrıntılı ve esas açıklaması için
[SYSTEM_ARCHITECTURE.md](SYSTEM_ARCHITECTURE.md) belgesini kullanın. HTTP örnekleri
[API_EXAMPLES.md](API_EXAMPLES.md) içindedir.

> Kaynak kod (`backend/app`, `frontend/src`), konfigürasyon ve Compose dosyaları
> davranış için asıl kaynaktır. Eski “faz” adları üretim hazır olma beyanı değildir.

## Ürün ve ana akışlar

Involo, Instagram Business üreticileri için içerik istihbaratı sağlar:

1. Resmî Meta Hashtag API ile herkese açık trend içeriğini keşfeder.
2. İçeriği zenginleştirir, zaman serisi snapshot'larıyla skorlar ve yaşam döngüsü
   (`emerging`, `rising`, `saturated`, `declining`) çıkarır.
3. Videoyu S3'e alır; keyframe/segment üretir; Nova Pro ile görsel analiz ve Nova 2
   Multimodal Embeddings ile `text`, `audio_video`, `fused` vektörleri oluşturur.
4. Instagram Login ile bağlanan üreticinin son 30 gündeki en fazla 30 içeriğinden
   performans profili, semantik içerik sütunları ve profil özeti üretir.
5. Qdrant'tan filtreli trend kanıtı getirir; skor, çeşitlilik ve geçmişe göre yeniden
   sıralayıp çekim brifi oluşturur.
6. Kaydetme/reddetme/yayınlama olaylarını, öneri-post bağlantısını, 24/72 saatlik
   sonucu ve deney durumunu saklar.

Google Trends, YouTube ve Reddit bağlayıcıları isteğe bağlı konu sinyalleridir.
Bunların skorları Instagram metrikleri veya Insights olarak yorumlanmaz.

## Çalışan bileşenler

| Bileşen | Teknoloji | Sorumluluk |
|---|---|---|
| Web | Next.js 16, React 19 | Kullanıcı, onboarding, profil, öneri ve admin UI |
| API | FastAPI, Python 3.12 | Cookie auth, RBAC, OAuth, API, job dispatch |
| Worker | Celery | Scrape, pipeline, profiling, snapshot, outcome, topic işleri |
| Beat | Celery Beat | Dakikalık DB-cron ve saatlik istihbarat dispatch |
| MongoDB | MongoDB 8 | Ana kayıt ve iş durumu |
| Redis | Redis 7 | Broker/backend, lock, rate limit, OAuth state, log |
| Qdrant | V2 named vectors | Trend, kullanıcı, profil ve segment retrieval |
| S3/MinIO | Nesne deposu | Medya, keyframe, video segmenti, transcript staging |
| Bedrock/AWS | Nova Pro, Nova embedding, Transcribe | Görsel analiz, embedding, üretim, transcript |
| Meta | Instagram Login ve Graph API | OAuth, owned Insights ve hashtag trend kaynağı |

`docker-compose.infra.yml` yalnız MongoDB, Redis, Qdrant, MinIO ve bucket
başlatmayı içerir. API/worker/beat/frontend, yerel ortamda `run.sh` ile başlatılır.
Compose içindeki `latest` Qdrant/MinIO etiketleri üretimde sabit digest ile
değiştirilmelidir.

## Kod haritası

```text
backend/app/
  api/             FastAPI uygulaması, route, dependency, response
  core/            Ayar, auth, crypto, rate limit, cron, hata sınıflama
  schemas/         Pydantic HTTP ve domain kontratları
  services/        İş akışı ve deterministik iş kuralları
  providers/       Meta, AWS, Bedrock ve konu sinyali adaptörleri
  infrastructure/  Mongo/Redis/Qdrant, migration, log bus
  workers/         Celery app, scheduler, lock, task yaşam döngüsü
frontend/src/
  app/             Next.js App Router sayfaları
  components/      Özellik ve UI bileşenleri
  lib/             API client, tipler, validation
docs/
  SYSTEM_ARCHITECTURE.md  Üretim mimarisi ve operasyonel gerçekler
  API_EXAMPLES.md         Güncel HTTP örnekleri
```

## Gerçek provider davranışı

Çalışan provider factory'leri production provider'larını kurar. Embedding, profil
özeti ve öneri üretimi için runtime fake/fixture seçicisi yoktur; testler test double
kullanır. Credential olmadan yalnız altyapı ve provider gerektirmeyen akışlar
çalışabilir.

| İşlev | Provider / varsayılan |
|---|---|
| Trend keşfi | `meta` (resmî hashtag API); isteğe bağlı `instagram` Playwright |
| Creator tracking | `graph_api` (varsayılan), `fixture`, `playwright` |
| Metadata | `meta` |
| Transcript | AWS Transcribe |
| Görsel analiz | `eu.amazon.nova-pro-v1:0`, `eu-central-1` |
| Embedding | `amazon.nova-2-multimodal-embeddings-v1:0`, 1024, in-region `us-east-1` |
| Profil ve öneri | EU Nova Pro inference profile, `eu-central-1` |
| Marka referans analizi | Instagram Graph API / Business Discovery (admin-only) |

Public Meta discovery may omit views and follower counts. Enrichment therefore chains
metadata providers: official discovery is tried first, then the Meta Graph API (which can
return engagement for photo posts where yt-dlp has no video), then public yt-dlp
extraction. The `INVOLO_METADATA_FALLBACK_PROVIDER` setting controls which fallbacks
are enabled (`none`, `ytdlp`, `graph`, or `all`).
Scraped caption and media URL are preserved as the canonical content record during
metadata enrichment. The local `fake` transcription provider intentionally returns an
empty transcript; visible transcripts require `aws` plus a configured Transcribe S3
bucket and reachable media URLs.

Instagram CDN media URLs are signed and expire. Above-threshold items are therefore
downloaded to the primary S3 bucket during enrichment (`media_asset`); transcription
and the embed step reuse that stored original instead of the CDN URL. A download
that fails with HTTP 403 marks the item `media_expired` (terminal, outside the
retry loops); the scraper treats `media_expired` posts as missing and re-scrapes
them, and the upsert resets them to `discovered` with the fresh URL. Videos without
an audio stream yield an empty transcript instead of an enrichment failure.

Playwright adapter'ın bulunması onu resmî API'ye production fallback yapmaz;
platform koşulları ve müdahale gerektiren challenge/2FA yolları dikkate alınmalıdır.

## V2 veri modeli özeti

MongoDB ana kayıttır. Başlıca collection'lar: `users`, `auth_sessions`,
`trend_content`, `job_runs`, `instagram_connections`, `user_content`,
`user_profiles`, `recommendations`, `content_metric_snapshots`,
`audience_snapshots`, `user_preferences`, `recommendation_events`,
`recommendation_post_links`, `recommendation_experiments`, `provider_runs`,
`topic_signal_snapshots`, `topic_signal_aggregates`, `ranking_predictions`,
`evaluation_runs`, `schema_migrations`, `brand_analysis_posts`,
`brand_analysis_reports`.

Qdrant:

| Collection | Named vectors |
|---|---|
| `trend_content_v2` | `text`, `audio_video`, `fused` |
| `user_content_v2` | `text`, `audio_video`, `fused` |
| `user_profiles_v2` | `profile` |
| `content_segments_v2` | `segment` |

Normal `embed` işi, zenginleştirilmiş ve media URL'si bulunan trendleri tam S3/Nova
multimodal akışından geçirir. `multimodal-backfill` aynı implementasyonu eski schema
sürümü veya eksik visual analysis/segment verisi bulunan kayıtları onarmak için
kullanır. Kullanıcı içeriği ve profil vector backfill'i per-user sync veya toplu
`profile_all` profiling ile yapılır.

Primary media/video/keyframe/segment bucket'ı `eu-central-1`'dedir. Nova Pro vision
buradan çalışır. Nova 2 multimodal embedding için coğrafi inference profile
bulunmadığından model `us-east-1`'de plain in-region ID ile çağrılır; keyframe ve
segmentler ayrı `us-east-1` embedding-media bucket'ına
`content-intelligence/embedding/...` altında mirror edilir. Source video mirror
edilmez. Mongo `embedding_asset` ve `processing_regions`, Qdrant payload ve provider
telemetry gerçek region provenance'ını saklar.

## API özeti

- Health: `GET /health/live`, `GET /health/ready`
- Auth: `POST /api/v1/auth/{register|login|refresh|logout}`, `GET .../me`
- Tercihler: `GET|PUT /api/v1/preferences`
- Instagram: OAuth start/callback, status, disconnect
- Profil: sync ve analytics
- Öneriler: oluşturma/geçmiş, event, post-link
- Creator tracking: `POST|GET /api/v1/creators`, `GET .../creators/{id}`,
  `.../followers?range=week|month|year`, `.../content`, `POST .../analyze`,
  `DELETE .../creators/{id}`
- Deney: oluşturma ve state update
- Admin: scraper, pipeline, profiling, brand analysis, jobs, overview,
  observability, offline evaluation (`POST /admin/evaluations/run`) ve WebSocket log

Cookie auth kullanılır; browser `credentials: "include"` göndermelidir. Access ve
refresh cookie'leri HttpOnly'dir. Admin rolü güncel Mongo kullanıcısından doğrulanır.

## Marka referans analizi

Instagram marka referans analizi (`brand_analysis`) Celery görevi olarak
`analyze_brand` ile çalışır. `BrandAnalysisService` hesap adını/URL'sini çözümler,
Instagram Graph API ile gönderileri `brand_analysis_posts` koleksiyonuna kaydeder,
her post için `MediaProvider` ile S3'e indirir, `VisionProvider` ile görsel/video
analizi, `BrandCaptionAnalyzer` ile caption analizi üretir ve sonuçları MongoDB'ye
yazar. Servis ayrıca gönderilerden anlamsal etiketler (`semantic_tags`), içerik
işlevleri (`content_job`), olağandışı gönderi adayları (`anomaly_candidate`) ve
premium sinyaller çıkarır; organik ve olağandışı içerikleri ayırır, etkileşim
metriklerini `basis`/`confidence`/`comparable` ile açıkça ayırır.

`BrandAnalysisReportContext` artık `semantic_observations` (kanıt zincirleri),
`brand_world`, `content_recipe` ve `performance_summary` gibi yapılandırılmış
özetler içerir. `BrandAnalysisReportProvider` bu bağlamdan
`BrandAnalysisStrategicBrief` üretir, ardından dokuz bölümlük kanonik Markdown
rapora dönüştürür: Yönetici Özeti, Marka Dünyası, Müşteri Neden Beğenebilir,
Kanıt Zincirleri, İçerik Reçetesi, Performans Kanıtları, Çıkarılamayacak Sonuçlar,
Stratejik Kararlar (3-5 adet) ve Referans Gönderi Galerisi. Rapor
`brand_analysis_reports` koleksiyonunda hem `markdown_text` hem `strategic_brief`
olarak, S3'te `reports/brand/{job_id}/report.md` yolunda saklanır.

`GET /api/v1/admin/brand-analysis/reports/{id}` artık `schema_version`,
`markdown_text`, `strategic_brief` ve bounded `media_evidence` döndürür.
Frontend `brand-analysis-report.tsx` yapılandırılmış kısa raporu ve güvenli
Markdown preview arasında geçiş yapabilir; doğruluk payı rozetleri, metrik bazı
ve kanıt zincirleri erişilebilir şekilde sunar.
Admin endpoint'leri `GET /api/v1/admin/brand-analysis/runs/{id}`,
`/posts`, `/reports/{id}` ve WebSocket `/runs/{id}/logs` yollarıyla izlenir.
Yerel geliştirme için `INVOLO_BRAND_ANALYSIS_PROVIDER=fake` tüm LLM/S3
entegrasyonlarını sahte sağlayıcılarla değiştirir.

## Creator tracking

Herkese açık Instagram creator'ları günlük olarak izlenir. `track_creator` ve
`track_all_creators` Celery görevleri `CreatorTrackingService`'i çalıştırır:
profil çekimi (`CreatorProfileProvider` — Instagram Graph API Business
Discovery, JSON fixture veya headless Playwright; 401/403 `NeedsInterventionError`,
429/5xx `TransientError`), günlük takipçi snapshot'ı (`creator_snapshots`, gün
başına tek kayıt, tekrar çalıştırmada güncellenir), içerik diff'i (yeni gönderiler
transkripsiyon + multimodal embedding + viral skor, mevcut gönderiler yalnız
metrik/skór güncellemesi), trend skoru (0.7 · son 5 içerik ortalama viral skoru +
0.3 · haftalık takipçi büyümesi) ve AI profil (`creator_profiles`: pillar'lar,
`ai_summary`, niche'ler).

`INVOLO_CREATOR_TRACKING_PROVIDER` üç değer alır: `graph_api` (varsayılan, resmî
Meta Business Discovery), `fixture` (test/local JSON) ve `playwright` (headless
Chromium ile Instagram `web_profile_info` API'si). Playwright modu
`INVOLO_CREATOR_TRACKING_HEADLESS` ile görsel pencereyi açmadan çalışır, aynı
oturum/state dizinini (`INVOLO_SCRAPER_STORAGE_STATE_PATH`) ve Instagram
giriş bilgilerini (`INVOLO_INSTAGRAM_USERNAME` / `INVOLO_INSTAGRAM_PASSWORD`)
scraping'teki gibi kullanır. Yeni oturum başlatılırken kaydedilmiş
`instagram.json` çerezleri yüklenir; profil sayfasındaki "Log in / sign up"
modalı kapatılarak herkese açık profiller oturumsuz görüntülenmeye çalışılır.
Yalnızca Instagram `web_profile_info` API 401/403 döndüğünde veya tarayıcı
giriş sayfasına yönlendirdiğinde tam oturum/açık giriş yapılır.

Veri global olarak bir kez saklanır (`tracked_creators`, `creator_snapshots`,
`creator_content`, `creator_profiles`); kullanıcı erişimi
`user_tracked_creators` link koleksiyonu ile yönetilir — iki kullanıcı aynı
creator'ı eklediğinde ikinci scraping olmaz, `DELETE` yalnız linki kaldırır.

Scheduler sabit günlük saatte çalışır (varsayılan cron `0 0 * * *` = 03:00
UTC+3, `INVOLO_CREATOR_TRACKING_SCHEDULE_CRON`); kullanıcı konfigüre edemez. Son
çalışma `creator_tracking_config` içinde saklanır. Blok/challenge/rate limit
`needs_intervention` durumuna düşer; bypass edilmez. Vektörler
`creator_content_v2` Qdrant koleksiyonunda (text/audio_video/fused, user
content ile aynı şema).

Yerel geliştirme/testler için `INVOLO_CREATOR_TRACKING_PROVIDER=fixture`
(`INVOLO_CREATOR_TRACKING_FIXTURE_PATH`) ağsız JSON fixture provider'ı seçer.
`INVOLO_CREATOR_AI_PROFILE_ENABLED=false` AI maliyetini kapatır; metrikler ve
snapshot'lar yine güncellenir. Frontend `/creators` listesi ve
`/creators/{id}` detayı (takipçi grafiği, AI profil, içerik grid'i,
"Analyze now" butonu) sunar.

## Güvenlik ve veri silme sınırı

Argon2 parola, kısa JWT access cookie, hash'li/rotate refresh token, tek kullanımlık
Redis OAuth state, şifreli Meta token, CORS allowlist, güvenlik header'ları, Redis
rate limit ve generation lock uygulanmıştır. Sosyal metin/OCR model için güvenilmeyen
veri olarak işaretlenir; forced tool schema ve sunucu tarafı kanıt hydration kullanılır.

`DELETE /instagram/connection`, önce `user_content` içinden primary ve nested
embedding-media asset'lerini toplar ve her iki region/bucket'tan 1.000'lik retry-safe
batch'lerle siler; ardından
`user_profiles_v2`, `user_content_v2`, `content_segments_v2` point'lerini `user_id`
ile ve ilgili Mongo derived kayıtlarını siler. Herhangi bir store adımı başarısız
veya S3 delete kısmi olursa 204 dönmez; `503
instagram_disconnect_erasure_unavailable` döner. Tekrar çağrı, tamamlanmış silme
adımlarını güvenle yineler.

`users` ve `auth_sessions` kasıtlı olarak korunur. Bu nedenle işlem Instagram
bağlantısı ve derived business data erasure'dır; Involo hesap kapatma değildir.

## Offline değerlendirme ve telemetry

Öneri üretimi, ranked adayları ve olasılıklarını
`retrieval-filtered-fused-mmr-v2` model sürümüyle `ranking_predictions` içine yazar.
Admin değerlendirmesi model/cutoff/K alır; cutoff öncesi explicit etiketleri veya
tahmin sonrası snapshot'ta ranking medyanının üstündeki view sonucunu tarihsel label
olarak kullanır. Label yoksa 409 döner.

`offline-ranking-v1`; NDCG@K, Precision@K, Brier, 10 reliability bucket, p95 latency
ve prediction başına maliyet hesaplar. Varsayılan eşikler sırasıyla minimum 0.5,
minimum 0.2, maksimum 0.25, maksimum 30 saniye ve maksimum 1'dir. Önceki aynı-model
run'a göre NDCG/Precision 0.1 düşer veya Brier 0.05 artarsa rollback önerilir; otomatik
rollback yapılmaz.

Instrument edilmiş transcription ve Nova vision/text/video/image embedding çağrıları
`provider_runs` içine secret/prompt olmadan provider, model, stage, state, süre,
media süresi, subject ve gerektiğinde user kimliği yazar.
`GET /admin/observability` bunları provider/model/stage bazında toplar. Admin UI
queue/job/snapshot/funnel verisinin yanında telemetry tablosu, maliyet, kalite
eşikleri, son evaluation ve evaluation çalıştırma formunu gösterir.

## Yerel çalıştırma

```bash
cp .env.example .env
# Geliştirme JWT secret'ını değiştirin; provider akışları için gerçek
# Meta/AWS/S3/Bedrock değerlerini sağlayın.
./run.sh up
```

- UI: `http://localhost:8020`
- API: `http://localhost:8021`
- OpenAPI: `http://localhost:8021/docs`
- MinIO: `http://localhost:8027`

Manuel:

```bash
docker compose -f docker-compose.infra.yml -p involo up -d --wait
docker compose -f docker-compose.infra.yml -p involo --profile init run --rm minio-init

cd backend
uv sync --extra dev --quiet
source .venv/bin/activate
playwright install chromium
uvicorn app.main:app --reload

# Ayrı terminaller
celery -A app.tasks worker --loglevel=INFO
celery -A app.tasks beat --loglevel=INFO

cd ../frontend
npm install
npm run dev
```

## Doğrulama komutları

```bash
cd backend
uv run ruff check .
uv run mypy app
uv run pytest

cd ../frontend
npm run lint
npm run typecheck
npm test -- --run
npm run build

docker compose -f docker-compose.infra.yml config --quiet
```

Credential gerektiren smoke testler normal doğrulamada skip edilir. Yalnız açıkça
`1` yapılan flag çalışır:

- `INVOLO_RUN_REAL_AWS_SMOKE`: Bedrock embedding/profil/öneri ve Transcribe erişimi
- `INVOLO_RUN_REAL_S3_SMOKE`: media S3 put/get/delete
- `INVOLO_RUN_REAL_MEDIA_SMOKE`: iki regional bucket'a image/video yükleme, Nova
  embedding ve Nova Pro vision
- `INVOLO_RUN_REAL_INSTAGRAM_SMOKE`: Playwright discovery
- `INVOLO_RUN_REAL_INSTAGRAM_PROFILE_SMOKE`: test token ile Graph profil
- `INVOLO_RUN_REAL_META_SMOKE`: resmî hashtag ve Graph profil

Normal `pytest`/`make verify` sonucu canlı provider erişimini kanıtlamaz; aşağıdaki
canlı sonuç ayrı opt-in çalışmaya aittir.

## Bilinen operasyonel sınırlar

- Mongo/Qdrant/S3 arasında transaction yoktur; idempotent tekrar ve reconciliation
  gerekir.
- API'nin job kaydı ile broker dispatch'i atomik değildir; queued orphan izlenmelidir.
- S3 retention metadata/tag tek başına silme yapmaz; bucket lifecycle şarttır.
- Model/vector boyut değişikliği startup migration değildir; yeni collection ve
  kontrollü backfill, offline evaluation, cutover/rollback gerekir.
- `/health/ready`, Mongo/Redis/Qdrant yanında primary/embedding/transcribe S3
  `HeadBucket`, Bedrock foundation-model veya inference-profile metadata ve Meta
  token/account probe'ları yapar. AWS bucket'larında `GetBucketLocation` ile beklenen
  region doğrulanır; uyuşmazlık `region_mismatch` olur. Probe'lar 3 saniye timeout ve
  process başına 30 saniye cache varsayılanına sahiptir; sanitize reason ve region
  döner.
- Development'ta `INVOLO_PROVIDER_READINESS_PROBES_ENABLED=false` kullanılabilir;
  production bunu reddeder. Geçici provider kesintisi startup'ı durdurmaz veya
  liveness'ı bozmaz, fakat cache yenilenene kadar readiness 503 döndürür.
- Topic signal provider hataları izole edilir; recommendation rank formülüne şu an
  doğrudan katılmaz.
- Deney state machine'i vardır; otomatik trafik atama veya istatistiksel anlamlılık
  hesabı yoktur.
2026-07-17 canlı doğrulama kaydı: AWS opt-in suite içinde text embedding, profile
generation, recommendation ve Transcribe API testleri çalıştı; **4 passed**.
Media S3/Nova image-video ve Meta smoke testleri gerekli iki media bucket'ı ve Meta
konfigürasyonu bulunmadığı için çalıştırılmadı. Bu sonuç production deploy iddiası
değildir.
