# Pipeline Canlı Test Raporu — 2026-07-28

Kapsam: scraper (Instagram Playwright), enrichment (Meta metadata + AWS
Transcribe), embedding (S3 + Nova Pro vision + Nova 2 multimodal + Qdrant).
Tüm sağlayıcılar **gerçek** (fake/fixture yok): `INVOLO_EMBEDDING_PROVIDER=aws`,
`INVOLO_VISION_PROVIDER=aws`, `INVOLO_TRANSCRIPTION_PROVIDER=aws`,
`INVOLO_MEDIA_PROVIDER=s3`, `INVOLO_SCRAPER_ADAPTER=instagram`.

## Ortam doğrulaması

- `./run.sh up` ile infra (MongoDB, Redis, Qdrant, MinIO) + API (8021) +
  worker + beat + frontend (8020) başlatıldı.
- `GET /health/ready` → `ready`; mongo, redis, qdrant, media_s3,
  embedding_media_s3, transcribe_s3, meta_token, meta_account,
  bedrock_embedding/vision/profile/recommendation tümü `ok` (us-east-1).

## Adım 1 — Scraper (gerçek Instagram, Playwright)

- `POST /api/v1/admin/scraper/runs` → job `be6286ab…` → **succeeded**.
- Sayaçlar: `discovered: 3, inserted: 3, updated: 0, failed_keywords: 0`.
- Keyword `travel`: 90 reel görüldü, 84 `too_old` (>30 gün), 3 `existing`,
  3 yeni reel MongoDB'ye yazıldı. Gerçek tarayıcı oturumu ile çalıştı;
  challenge/2FA olmadı.

## Adım 2 — Enrichment (gerçek Meta + AWS Transcribe)

- `POST /api/v1/admin/pipeline/enrich` → job `7067bcae…` → **succeeded**.
- Sayaçlar: `processed: 106, scored: 57, enriched: 7, transcribed: 4,
  skipped_threshold: 50, failed: 49, needs_intervention: 0`.
- Metadata gerçek Meta API'den (ör. `DairFYPM3qP`: views=100.073.661,
  likes=11.554.975, viral_score=56.71 → threshold 40 üstü → transcribe).
- Transcribe gerçek AWS'de çalıştı (ör. `DairFYPM3qP` → `en-US` transcript).
- `views=0` görülen kayıtlar bilinen Meta hashtag API sınırı; scorer bounded
  fallback kullanıyor (dokümante davranış, hata değil).

### Bulunan hata (kök neden)

49 kayıt `video download failed: 403 Forbidden` ile `failed` oldu. Hepsinin
`enrichment_error` değeri aynı: Instagram CDN (`*.fbcdn.net`) imzalı URL'leri
süreli; eski scrape'lerden kalan `video_url` değerleri expire olmuş. Enrichment
download'ı doğrudan bu stale URL'den yapıyor.

## Adım 3 — Embedding (gerçek S3 + Bedrock + Qdrant)

- `POST /api/v1/admin/pipeline/embed` → job `69b77de8…` → **succeeded**.
- Sayaçlar: `processed: 57, embedded: 7, failed: 50`.
- 7 item tam gerçek multimodal akıştan geçti (ör. `DairFYPM3qP`: 4.54 MB
  indirilip S3'e yazıldı, 1 keyframe + 1 segment ffmpeg ile çıkarıldı, Nova Pro
  vision analizi, Nova text + video-segment + keyframe embedding'leri, Qdrant
  upsert).
- Qdrant doğrulaması: `trend_content_v2` → 7 point, named vectors
  `text`/`audio_video`/`fused`; `content_segments_v2` → 14 point.
- `provider_runs` telemetrisi: `vision` 7/7, `embedding_text` 7/7,
  `embedding_video` 7/7, `embedding_image` 7/7 succeeded (amazon_bedrock,
  nova-2-multimodal-embeddings, us-east-1). Mongo `processing_regions`
  gerçek region provenance'ını doğruluyor.
- Embed'deki 50 başarısızlık aynı kök neden: `media download failed`
  (expire olmuş CDN URL'leri). Yalnız taze scrape edilen 3 + son dönemde
  indirilebilir durumdaki 4 item başarılı oldu.

## Frontend kontrolü (Playwright)

- `http://localhost:8020` login → dashboard → admin overview sorunsuz.
- Admin "Content pipeline" kartı API ile birebir aynı: Enriched 50, Stored 50,
  Embedded 7; "Attention needed" 49 failed item'ı gösteriyor.
- Son job'lar tablosu scrape/enrich/embed `succeeded` satırlarını doğru
  listeliyor; telemetri tablosu Nova/Transcribe run sayıları ve latency'leri
  gösteriyor.
- Konsol hataları yalnız login öncesi beklenen 401'ler (`/auth/me`,
  `/auth/refresh`); uygulama hatası yok.

## Sonuç

| Adım | Durum | Gerçek mi? | Not |
|---|---|---|---|
| Scraper | PASS | Evet (Playwright/Instagram) | 3 yeni reel |
| Enrichment | PASS (kısmi veri kaybı) | Evet (Meta + Transcribe) | 49/106 CDN 403 |
| Embedding | PASS (kısmi veri kaybı) | Evet (S3/Nova/Qdrant) | 50/57 media download failed |
| Frontend | PASS | — | Veri API ile tutarlı |

## Hata düzeltme planı

### 1. [Ana hata] Expire olmuş Instagram CDN URL'leri (~100 item kullanılamıyor)

Kök neden: `video_url` scrape anındaki imzalı CDN URL'si; saatler içinde
expire oluyor. Enrich/embed aşamaları bu stale URL'den indirmeye çalışıyor.

Önerilen düzeltmeler (öncelik sırasıyla):

- **1a. Medyayı erken S3'e al.** Enrichment metadata çözümleme sonrası
  (veya scrape sırasında) video derhal primary S3 bucket'ına indirilip
  `s3://...` referansı Mongo'ya canonical media olarak yazılmalı; sonraki
  tüm aşamalar (transcribe, embed) yalnız S3 key'i kullanmalı. Tekrarlanan
  job'larda CDN expire sorunu tamamen ortadan kalkar.
- **1b. Download öncesi URL yenileme.** 403 alındığında (veya `taken_at`/`first_seen_at`
  eskiyse) metadata provider ile media URL'sini yeniden çözümleyip tek retry
  yapılmalı; başarılıysa Mongo'daki `video_url` güncellenmeli.
- **1c. Geri dönüşsüzleri ayıkla.** Yenileme de 403 verirse kayıt
  `needs_intervention` yerine terminal bir `media_expired` durumuna alınıp
  embed eligibility'den düşürülmeli; admin UI'da "yeniden scrape et" aksiyonu
  sunulmalı.

### 2. [İzleme] Job log listesi sınırlı

Enrich job'unda `failed: 49` sayacına karşılık job `logs` dizisinde yalnız
4 hata satırı görünüyor (log bus bounded). Hata analizi için Mongo
`enrichment_error` agregasyonu gerekti. Admin UI'a per-item failure summary
(gruplanmış hata sayıları) eklenmesi operasyonu kolaylaştırır.

### 3. [Bilinen sınır, hata değil]

- Meta hashtag API `views`/follower döndürmüyor → scorer bounded fallback
  (dokümante).
- Admin overview'daki 7/27 tarihli 2 failed scrape job'u, chromium binary
  eksikliğindendi; `playwright install chromium` sonrası bugünkü run başarılı.
- Transcribe telemetrisindeki 68 historical failure bu run'a ait değil
  (bugün 4/4 succeeded).

## Yeniden üretme komutları

```bash
./run.sh up
curl -s -c cj -X POST localhost:8021/api/v1/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"email":"<admin>","password":"<pass>"}'
curl -s -b cj -X POST localhost:8021/api/v1/admin/scraper/runs -d '{}'
curl -s -b cj -X POST localhost:8021/api/v1/admin/pipeline/enrich
curl -s -b cj -X POST localhost:8021/api/v1/admin/pipeline/embed
curl -s -b cj localhost:8021/api/v1/admin/pipeline/runs/<task_id>
```

---

# Uygulanan düzeltmeler ve canlı doğrulama (2026-07-28, ikinci oturum)

## Değişiklikler

1. **S3-first medya akışı** — `backend/app/services/enrichment.py`: threshold üstü
   item'ların videosu enrichment sırasında primary S3 bucket'ına indirilip
   `media_asset` olarak Mongo'ya yazılıyor; transcription artık expire olan CDN
   URL'si yerine S3 presigned URL'den çalışıyor.
2. **Embed medya yeniden kullanımı** — `backend/app/services/multimodal.py`:
   `media_asset`'i olan item'lar embed sırasında yeniden indirilmiyor
   ("Reusing stored media"); yoksa eski davranış (ingest) korunuyor.
3. **`media_expired` terminal durumu** — `app/core/errors.py`
   `is_expired_media_error` (403 cause-chain sınıflandırması). Enrich ve embed
   aşamalarında CDN 403 → `media_expired`; retry döngülerinden çıkıyor.
   Scraper (`app/services/scraper.py`) bu postları yok sayıp yeniden scrape
   ediyor; upsert taze URL ile durumu `discovered`'a çekiyor.
4. **Sesiz videolar** — `app/providers/transcription.py`: ses track'i olmayan
   videolar (`Output file #0 does not contain any stream`) artık boş transcript
   döndürüyor; item fail olmuyor. ffmpeg hata mesajları artık stderr'in sonunu
   (gerçek hata) tutuyor, build banner'ı değil.

## Testler

- Yeni regression testleri: `tests/test_enrichment.py` (3), `tests/test_multimodal_segments.py`
  (2), `tests/test_scraper_service.py` (1), `tests/test_transcription.py` (2).
- `ruff check` + `mypy app` temiz; **213 passed, 9 skipped**.

## Canlı doğrulama (gerçek sağlayıcılar)

- Embed re-run: 50 stale item `media_expired` oldu (retry loop bitti, ~20 sn).
- Enrich re-run: 49 eski "failed" → `media_expired`; 50 stored yeniden skorlandı, 0 fail.
- Scrape re-run (`travel`): 3 yeni reel.
- Full pipeline: `Da1j0FPTLg6` enrich sırasında S3'e alındı (`media_asset`
  `content-intelligence/.../media/original.mp4`), embed "Reusing stored media"
  ile CDN'e hiç dönmeden embedded. Qdrant `trend_content_v2` = 8 point.
- Sesiz video `Daj_073JW4E` önce `failed` idi; düzeltme sonrası boş transcript
  ile `enriched` → embed ile `embedded` (Qdrant = 9 point).
- Son durum: embedded 9, enriched 0, stored 51, media_expired 99, failed 0.

## Kalan bilinen sınır

- `media_expired` item'lar ancak scraper aynı postu feed'de tekrar
  görürse kurtarılır (feed artık göstermiyorsa kurtarılamaz; beklenen davranış).
- En azından 99 eski kayıt bu kategoride; gerçek Instagram akışında yeni
  içerik sürekli geldiği için pipeline ilerlemeye devam ediyor.
