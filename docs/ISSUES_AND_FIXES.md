# Involo POC — Bulunan Hatalar ve Çözüm Adımları

> **Test tarihi:** 2026-07-31  
> **Ortam:** Yerel (`run-local.sh`), backend `http://localhost:8021`, frontend `http://localhost:8020`  
> **Kaynak rapor:** `docs/E2E_TEST_REPORT.md`

Bu dokümanda test sırasında gözlemlenen tüm hatalar, kritiklik sıralarına göre listelenmiş ve her biri için **köken sebep**, **düzeltilecek dosyalar** ve **önerilen çözüm adımları** verilmiştir.

---

## Özet Tablo

| ID | Hata | Önem | Hızlı Çözüm |
|----|------|------|-------------|
| BUG-1 | `run-local.sh` `localtunnel` hatasında tüm servisler duruyor | Yüksek | `INVOLO_NGROK_DOMAIN` yönetimini değiştir; fallback ekle |
| BUG-2 | MCP Playwright navigasyon sırasında transport kapandı | Orta | MCP sunucusunu yeniden başlat; alternatif e2e çalıştır |
| BUG-3 | Trend content `enrichment_error: "Invalid endpoint: "` | Yüksek | Boş region/endpoint URL'lerini normalize et; hata bağlamını genişlet |
| BUG-4 | Bedrock `eu.*` model ID'leri %100 hata | Yüksek | `eu.*` default'larını `us.*` yap; model ID ↔ region eşleştirmesi ekle |
| BUG-5 | AWS Transcribe `identify-language` ~%39 başarısız | Orta | Hata sebebini ayrıştır; ses format/boyut ve S3 URI kontrolü ekle |
| BUG-6 | Instagram scrape/creator tracking çöküyor / 429 / checkpoint | Yüksek | Fixture/fake provider seçeneği; rate-limit & retry politikası iyileştir |
| BUG-7 | Creator oluşturma var olmayan kullanıcı adını kabul ediyor | Orta | Varlık kontrolü ekle; oluşturmadan önce validate et |
| BUG-8 | `/admin/trend-content/{id}/` trailing slash 307 dönüyor | Düşük | Route veya UI çağrısında slash tutarlılığı sağla |
| BUG-9 | `admin/observability` eski kuyruk verileri (`attention_jobs` 31, `queue_age` 26 saat) | Orta | Eski `queued` işler için TTL/cleanup task ekle |
| BUG-10 | Dashboard ilk `GET /auth/me` 401 dönüyor (console hatası) | Düşük | Token refresh stratejisini `api.ts`'te optimize et |

---

## BUG-1: `run-local.sh` localtunnel başarısız olunca tüm sistem kapanıyor

**Önem:** Yüksek  
**Gözlem:** `.env`'de `INVOLO_NGROK_DOMAIN=auto` varken `run-local.sh up` localtunnel'dan URL alamayınca `exit 1` ile tüm uygulamayı durduruyor.

**Köken:**
- `run-local.sh` satırları 117–191: `start_public_tunnel` 3 denemeden sonra `public_url` boşsa `exit 1` yapıyor.
- Instagram OAuth callback için public URL zorunlu değil; yerel testlerde `localhost` yeterli.

**Düzeltilecek dosyalar:**
- `run-local.sh`
- `.env.example`
- `README.md`

**Çözüm:**
1. `start_public_tunnel` başarısız olursa sadece `PUBLIC_URL=http://localhost:8021` ile devam et; `exit 1` yapma.
2. `INVOLO_INSTAGRAM_OAUTH_REDIRECT_URI` tanımlanmadıysa `http://localhost:8021/api/v1/instagram/oauth/callback` olarak ayarla.
3. `.env.example` içinde `INVOLO_NGROK_DOMAIN` yorum satırı olsun ve README'de "public tunnel sadece canlı Meta uygulaması gerektiren testlerde gerekli" yazsın.

Önerilen `run-local.sh` değişikliği (satır 188 civarı):

```bash
if [ -z "$public_url" ]; then
    echo "WARNING: localtunnel could not start. Continuing with localhost."
    PUBLIC_URL="http://localhost:${port}"
    export INVOLO_INSTAGRAM_OAUTH_REDIRECT_URI="${PUBLIC_URL}/api/v1/instagram/oauth/callback"
fi
```

**Doğrulama:**
- `INVOLO_NGROK_DOMAIN=auto ./run-local.sh up` → localtunnel hatası vermeden devam etmeli.
- `INVOLO_NGROK_DOMAIN=` (boş) ile `up` çalışmalı.

---

## BUG-2: MCP Playwright transport kapandı

**Önem:** Orta  
**Gözlem:** Creators detay sayfasına tıklanırken `mcp3_browser_click` "transport closed" hatası verdi; sonraki MCP çağrıları da başarısız oldu.

**Köken:**
- MCP Playwright sunucusunun IDE bağlantısı veya kendi süreci çöktü; bu uygulama kodu değil, test altyapısı kaynaklı.

**Düzeltilecek dosyalar:**
- Test ortamı / IDE konfigürasyonu
- `frontend/playwright.config.ts`

**Çözüm:**
1. MCP Playwright sunucusunu yeniden başlat (IDE panelinden veya MCP sunucu ayarlarından).
2. Alternatif olarak UI testlerini doğrudan `frontend` dizininde çalıştır:
   ```bash
   cd frontend
   npm run test:e2e -- --project=chromium
   ```
3. Eğer Chromium eksikse:
   ```bash
   cd frontend && npx playwright install chromium
   # veya backend tarafı için:
   cd backend && uv run python -m playwright install chromium
   ```

**Doğrulama:**
- `mcp3_browser_navigate` yeniden çalışır hale gelmeli.
- `frontend/e2e/admin.spec.ts` ve `admin-scraper.spec.ts` başarılı/başarısız açık raporla çalışmalı.

---

## BUG-3: Trend content `enrichment_error: "Invalid endpoint: "`

**Önem:** Yüksek  
**Gözlem:** `GET /api/v1/admin/trend-content/{id}` ile `processing_status: failed` kayıtlarda `enrichment_error` alanı `"Invalid endpoint: "` (boş URL) olarak dolu.

**Köken:**
- `botocore.endpoint` `ValueError: Invalid endpoint: {endpoint_url}` mesajı, `boto3.client(...)` çağrısına **boş string** (`""`) `endpoint_url` veya `region_name` geçildiğinde fırlatılır.
- Yerel denemede şu iki durumun aynı hatayı verdiği doğrulandı:
  ```python
  boto3.client('s3', region_name='us-east-1', endpoint_url='')
  # ValueError: Invalid endpoint:

  boto3.client('transcribe', region_name='')
  # ValueError: Invalid endpoint: https://transcribe..amazonaws.com
  ```
- `app/providers/transcription.py` satır 100: `boto3.client("transcribe", region_name=self.settings.aws_region)` — `aws_region` boş string olabilir.
- `app/providers/brand_pdf.py` satır 132 ve `app/providers/brand_report.py` satır 568: `options = {"endpoint_url": self.settings.transcribe_s3_endpoint_url}` — eğer bu değer `None` yerine `""` ise aynı hatayı verir.

**Düzenlenecek dosyalar:**
- `backend/app/core/config.py`
- `backend/app/providers/transcription.py`
- `backend/app/providers/brand_pdf.py`
- `backend/app/providers/brand_report.py`
- `backend/app/services/enrichment.py`

**Çözüm:**
1. Tüm `*_endpoint_url` ve region alanları için `mode="before"` validator'lar ekleyerek boş string `""` -> `None` dönüşümünü sağla (`empty_endpoint_url_to_none` sadece iki alanı kapsıyor; genelleştirilmeli).
2. `transcription.py` satır 85 civarındaki `s3_options` ve `transcribe` client oluşturmadan önce `region_name` ve `endpoint_url`'nin boş olmadığını kontrol et; boşsa `ValueError` yerine anlamlı bir mesaj (`"INVOLO_AWS_REGION is empty"`) fırlat.
3. `brand_pdf.py` / `brand_report.py` S3 client kurulumunda `endpoint_url` yoksa `options` sözlüğüne eklenmemeli:
   ```python
   options: dict[str, Any] = {"region_name": self.settings.media_s3_region}
   if self.settings.transcribe_s3_endpoint_url:
       options["endpoint_url"] = self.settings.transcribe_s3_endpoint_url
   ```
4. `enrichment.py` satır 116 civarındaki `except Exception` bloğunda hatayı `str(exc)` yerine şu şekilde kaydet:
   ```python
   {
       "enrichment_error": str(exc),
       "enrichment_error_type": type(exc).__name__,
       "enrichment_provider": "transcription" # veya ilgili aşama
   }
   ```

**Doğrulama:**
- `backend/uv run python -m pytest tests/...` ile `enrich_trend_content` fixture/fake modunda çalıştırılmalı.
- Yeni scrape sonrası `trend_content` kayıtları `enrichment_error` alanı boş kalmalı veya anlamlı hata içmeli.

---

## BUG-4: Bedrock `eu.*` model ID'leri %100 hata

**Önem:** Yüksek  
**Gözlem:** `admin/observability` verilerine göre:
- `eu.amazon.nova-pro-v1:0` `brand_caption`: 15/15 hata
- `eu.amazon.nova-pro-v1:0` `brand_report`: 2/2 hata
- `eu.amazon.nova-lite-v1:0` `brand_caption`: 20/20 hata
- Aynı aşamalarda `us.amazon.nova-pro-v1:0` / `us.amazon.nova-lite-v1:0` başarılı.

**Köken:**
- `backend/app/core/config.py` satır 117, 134, 182'de default model ID'ler `eu.amazon.nova-pro-v1:0` ile başlıyor.
- `bedrock_generation_region` default `eu-central-1`, fakat `.env` genelde `us-east-1` olarak eziliyor.
- Bedrock inference profile ID'lerinin `us.` / `eu.` prefix'i endpoint bölgesiyle eşleşmeli. `us-east-1` endpoint ile `eu.` prefixli model çağrılmaz.
- `_derive_brand_analysis_model_ids` (satır 271–280) `brand_analysis_report_model_id` ve `brand_analysis_caption_model_id` üretirken vision model ID'den türetiyor; eğer vision `eu.` ise caption/report da `eu.` oluyor.

**Düzenlenecek dosyalar:**
- `backend/app/core/config.py`
- `.env.example`

**Çözüm:**
1. `backend/app/core/config.py` içinde default'ları `us-east-1` uyumlu inference profile ID'lerine çevir:
   ```python
   bedrock_vision_model_id: str = "us.amazon.nova-pro-v1:0"
   bedrock_profile_model_id: str = "us.amazon.nova-pro-v1:0"
   bedrock_recommendation_model_id: str = "us.amazon.nova-pro-v1:0"
   ```
2. `_derive_brand_analysis_model_ids` validator'ında `.replace("nova-pro", "nova-lite")` yapmadan önce prefix'in (`us.` / `eu.`) `bedrock_generation_region` ile tutarlı olduğunu doğrula veya `.env`'den zorunlu olarak set edilmesini iste.
3. Bir `@model_validator` ekle; `bedrock_*_model_id` ile `bedrock_generation_region` eşleşmiyorsa `ValueError` fırlat:
   ```python
   expected_prefix = self.bedrock_generation_region.split("-")[0]  # us / eu / apac
   for model_id in (self.bedrock_vision_model_id, ...):
       if not model_id.startswith(expected_prefix + "."):
           raise ValueError(f"{model_id} does not match region {self.bedrock_generation_region}")
   ```

**Doğrulama:**
- `admin/observability` provider usage tablosunda `eu.*` modellerde hata kalmamalı.
- `make verify` veya `uv run pytest` config testleri geçmeli.

---

## BUG-5: AWS Transcribe `aws-transcribe-identify-language` yüksek hata oranı

**Önem:** Orta  
**Gözlem:** `admin/observability`:
- `aws_transcribe` `aws-transcribe-identify-language` stage: 179 run, 70 failure (~%39).

**Köken:**
- Hata sebebi loglarda görünmüyor; muhtemelen ses dosyası format/boyut, S3 URI hatası, `Invalid endpoint` (BUG-3) veya `StartTranscriptionJob` parametreleri.

**Düzenlenecek dosyalar:**
- `backend/app/providers/transcription.py`
- `backend/app/workers/tasks/trends.py`

**Çözüm:**
1. `transcription.py` içinde `start_transcription_job` çevresinde try/except ekleyerek `ClientError` kodunu (`InvalidMediaUri`, `BadRequestException`, vb.) ve mesajı logla.
2. `MediaFileUri` oluşmadan önce `bucket` ve `key` değerlerini kontrol et; `bucket` boşsa ses dosyası yüklemeden önce hata fırlat.
3. `enrichment.py` veya `trends.py` `_enrich` içinde transcribe hatası oluşursa `counters["failed"]` artır ve `enrichment_error` alanına hem `error` hem `stage` (transcription) ve `provider` yaz.
4. Yerel testler için `transcription_provider = "fake"` veya fixture ses dosyası desteği ekle.

**Doğrulama:**
- `admin/jobs` içindeki `enrich` iş loglarında transcribe hatası ayrıntılı görünmeli.
- `aws_transcribe` failure oranı %5'in altına inmeli.

---

## BUG-6: Instagram scrape ve creator tracking çöküyor / 429 / checkpoint

**Önem:** Yüksek  
**Gözlem:**
- `scrape` işi: `BrowserType.launch_persistent_context: Executable doesn't exist ...` (Chromium kurulmamışken), sonrasında `Instagram login was not accepted; page message: checkpoint`.
- `creator_track` işleri: `Instagram profile API returned 429`, `upstream returned 429`, `BrowserContext.close ...`, `Meta token exchange failed: OAuthException code 101`.

**Köken:**
- `backend/app/core/config.py` satır 61'de `scraper_adapter: Literal["instagram", "meta"] = "meta"` default olarak Meta Graph API’ye bağlı; `creator_tracking_provider: Literal["graph_api", "fixture", "playwright"] = "graph_api"` (satır 142) da gerçek Meta API’ye bağlı.
- `backend/app/providers/scraper.py` satır 2065 `build_scraper` sadece `meta`/`instagram` seçeneklerini tanıyor; `fixture` adapter’ı yok.
- `run-local.sh` satır 90 `playwright install chromium >/dev/null 2>&1 || true` non-blocking çalıştığı için worker Chromium hazır olmadan başlayabiliyor.
- `InstagramScraper` Playwright login sırasında checkpoint/captcha ile karşılaştığında `NeedsInterventionError` fırlatıyor, ancak hata mesajı ve `needs_intervention` state akışı yeterince açık değil.
- `creator_profile.py` Graph API `OAuthException code 101` (`Invalid platform app`) şu an `CreatorProfileError`’a düşüp `failed` state atıyor; bu bir uygulama/konfigürasyon hatası olduğu için `needs_intervention` olmalı.
- `tasks/creator_tracking.py` ve `tasks/trends.py`’de 429/TransientError’lar için Celery backoff/jitter yetersiz.

**Düzenlenecek dosyalar:**
- `run-local.sh`
- `backend/app/core/config.py`
- `backend/app/providers/scraper.py`
- `backend/app/providers/creator_profile.py`
- `backend/app/workers/tasks/creator_tracking.py`
- `backend/app/workers/tasks/trends.py`
- `backend/app/workers/runtime.py`
- `README.md`

**Çözüm:**
1. **Yerel geliştirme default'u fixture/fake olsun:**
   - `config.py`’de `creator_tracking_provider` default’unu `fixture` yap.
   - `scraper_adapter` tipini `Literal["instagram", "meta", "fixture"]` yap ve default’u `fixture` yap.
   - `backend/app/providers/scraper.py` `build_scraper` içine `fixture` branch ekle (`FixtureTrendAdapter` veya `ScraperAdapter` tabanlı sahte veri üreten bir implementasyon).
   - `.env.example`’da `INVOLO_CREATOR_TRACKING_PROVIDER=fixture` ve `INVOLO_SCRAPER_ADAPTER=fixture` default olarak ver.
2. `run-local.sh` satır 90 civarında Chromium kurulumunu provider’a göre zorunlu ve blocking hale getir:
   ```bash
   if [ "${INVOLO_SCRAPER_ADAPTER:-fixture}" != "fixture" ] || [ "${INVOLO_CREATOR_TRACKING_PROVIDER:-fixture}" = "playwright" ]; then
       python -m playwright install chromium --with-deps
   fi
   ```
3. `scraper.py` `InstagramScraper` ve `MetaTrendAdapter`’da 429/TransientError için exponential backoff + jitter uygula; maksimum retry sınırı koy. `InstagramScraper` captcha/2FA/checkpoint durumunda `NeedsInterventionError` fırlat ve explicit mesaj (`"Instagram intervention required: captcha/2FA/checkpoint"`) ile `runtime.py` üzerinden `needs_intervention` state’e yaz.
4. `creator_profile.py` `_raise_for_graph_error`’da `OAuthException code 101` ve benzeri uygulama konfigürasyon hatalarını `NeedsInterventionError` yap. `Meta token exchange` hatası alınınca `needs_intervention` durumuna geç; tekrar tekrar token almaya çalışma.
5. `tasks/creator_tracking.py` ve `tasks/trends.py`’de Celery task decorator’lerine `autoretry_for=(TransientError,)`, `retry_backoff=True`, `retry_backoff_max=300`, `max_retries=3` ekle.
6. **Public postlar için tek kaynak Playwright scraper olsun:**
   - `backend/app/core/config.py` ve `.env.example` içinde `metadata_fallback_provider` default’u `none` yap; Graph API / yt-dlp fallback’leri herkese açık postlarda varsayılan olarak çalışmasın.
   - `backend/app/providers/scraper.py` `InstagramScraper._extract_post_metadata` içinde `media_type`, `video_duration`, `share_count` ve ek `view_count` alanlarını topla; `NeedsInterventionError` için `checkpoint`/`challenge`/`suspicious`/`restrict` gibi kelimeleri ve 401/403 yanıtlarını yakala.
   - `backend/app/providers/metadata.py` `_is_incomplete` mantığını medya türüne göre düzenle: fotoğraf/carousel için `video_duration`/`view_count` eksikliği fallback tetiklemesin; sadece `taken_at`, `owner_username` veya video/reels için `video_duration` eksikse fallback çalışsın.
   - `backend/app/providers/scraper.py` `_post_metadata` ve `_internal_api_metadata` içinde public `/p/{shortcode}/` ve `i.instagram.com/api/v1/media/{id}/info` sonuçlarını birleştir; `owner_follower_count` eksikse aynı context ile `i.instagram.com/api/v1/users/{pk}/info` çağrılabilir.

**Doğrulama:**
- `INVOLO_CREATOR_TRACKING_PROVIDER=fixture` ile `creator_track` işi başarılı olmalı.
- `INVOLO_SCRAPER_ADAPTER=fixture` ile scrape işi fixture verileriyle çalışmalı.
- Rate-limit durumunda Celery retry backoff logları görünmeli.
- Checkpoint/captcha durumunda job state `needs_intervention` ve mesaj explicit olmalı.

---

## BUG-7: Creator oluşturma var olmayan kullanıcı adını kabul ediyor

**Önem:** Orta  
**Gözlem:** `POST /api/v1/creators` ile `{"username":"not_a_real_user_xyz"}` 201 döndü; `status: active` oldu. Hemen ardından tracking işi başarısız olacak.

**Köken:**
- `backend/app/api/routes/creators.py` `add_creator` (satır 75-123) sadece regex (`_USERNAME_RE`) ve string normalize edip `tracked_creators` koleksiyonuna insert/upsert yapıyor; hesabın Instagram üzerinde gerçekten var olup olmadığını doğrulamıyor.
- `backend/app/services/creator_tracking.py` `run` (satır 91-116) asenkron `creator_track` işi başlattığı için, var olmayan kullanıcının hatası endpoint anında dönmemiş oluyor; job sonradan `not_found` durumuna düşüyor.
- `backend/app/providers/creator_profile.py` `GraphCreatorProfileProvider` ve `PlaywrightCreatorProfileProvider` zaten `CreatorNotFoundError` fırlatabiliyor; bu doğrulama endpoint’te de yapılabilir.

**Düzenlenecek dosyalar:**
- `backend/app/api/routes/creators.py`
- `backend/app/services/creator_tracking.py`
- `backend/app/providers/creator_profile.py` (varlık sorgusu için)
- `backend/app/core/config.py` (fixture modda kontrol atlanması)

**Çözüm:**
1. `add_creator` endpoint’inde, `INVOLO_CREATOR_TRACKING_PROVIDER` değerine göre hafif bir varlık doğrulaması yap:
   - `graph_api`: `/{business_account_id}?fields=business_discovery.username(username){id}` Graph API çağrısı; 803/100 veya boş yanıt → `404 Not Found`.
   - `playwright`: `InstagramScraper` ile profil sayfasının varlığı/HTTP 200 kontrolü; `ProfileFetchError` `not_found` ise 404.
   - `fixture`: doğrudan fixture map’te ara; yoksa 404.
2. Doğrulama başarılı olursa `201 Created`; başarısızsa `404 Not Found` veya `422 Unprocessable Entity` ile `{"detail": "Instagram user not found"}` dön.
3. Alternatif olarak (yüksek gecikme istenmiyorsa): endpoint hemen `202 Accepted` + `status: pending` dönsün; ilk başarılı `creator_track` çalıştıktan sonra `tracked_creators` dokümanı `active`/`not_found` güncellensin.
4. Kullanıcı adı normalizasyonunu güçlendir: küçük harf, başta/sonda nokta veya alt çizgi varsa reddet, uzunluk sınırı (1-30 karakter) uygula. Mevcut `_USERNAME_RE` (`^[a-z0-9._]{1,30}$`) yeterli ama başta/sonda `.` ve `_` olması durumunda ek uyarı dönülebilir.
5. `add_creator`’ın `CreatorProfileProvider` ile yapacağı varlık kontrolü, aynı provider üzerinden olacak şekilde refactor edilerek DRY prensibi korunsun.

**Doğrulama:**
- `POST /api/v1/creators -d '{"username":"not_a_real_user_xyz"}'` → `404`/`422` dönmeli.
- `POST /api/v1/creators -d '{"username":"majasrecipes"}'` → `201` dönmeli.
- `GET /api/v1/creators` ile `not_a_real_user_xyz` kaydı görünmemeli.

---

## BUG-8: `/admin/trend-content/{id}/` trailing slash 307 yönlendirmesi

**Önem:** Düşük  
**Gözlem:** `GET /api/v1/admin/trend-content/{id}/` 307 Temporary Redirect ile `/api/v1/admin/trend-content/{id}`'e yönlendiriyor.

**Köken:**
- `backend/app/api/routes/admin_trend_content.py` satır 119: `@router.get("/{content_id}", ...)` — trailing slash yok.
- FastAPI default'u `/path/` ile `/path` arasında 307 yönlendirmesi yapmaktır.

**Düzenlenecek dosyalar:**
- `backend/app/api/routes/admin_trend_content.py` veya `frontend/src/lib/api.ts`

**Çözüm:**
1. Router'da hem `/` hem `/` olmayan varyantı kabul et:
   ```python
   @router.get("/{content_id}")
   @router.get("/{content_id}/")
   async def get_trend_content(...):
       ...
   ```
2. Veya frontend `api.ts` içinde `getTrendContentDetail` çağrısını trailing slash olmayacak şekilde ayarla.

**Doğrulama:**
- `curl /api/v1/admin/trend-content/{id}/` → 200 dönmeli.

---

## BUG-9: `admin/observability` eski kuyruk verileri

**Önem:** Orta  
**Gözlem:** `admin/observability`:
- `queue_age_seconds`: 93.353 (yaklaşık 26 saat)
- `attention_jobs`: 31
- `stale_trends`: 50

**Köken:**
- Eski `queued` durumundaki işler (özellikle `recommendation_outcome`, `topic_signals`, `metric_snapshot`) ve `needs_intervention`/`failed` işler temizlenmemiş.
- Dashboard `attention_jobs` bunları `failed` + `needs_intervention` + `cancelled` gibi biriktiriyor.

**Düzenlenecek dosyalar:**
- `backend/app/services/jobs.py` veya `backend/app/workers/runtime.py`
- `backend/app/workers/beat_schedule.py` (varsa)
- `backend/app/api/routes/admin.py`

**Çözüm:**
1. Celery beat'e günlük `cleanup_stale_jobs` task ekle: 24 saatten eski `queued` işleri `failed` veya `cancelled` durumuna çek.
2. `admin/observability` `attention_jobs` hesaplamasına "stale" ayrımı ekle:
   - `attention_jobs` = `failed` (son 7 gün) + `needs_intervention`
   - Ayrıca `stale_jobs` sayacı göster.
3. `job_runs` koleksiyonuna `created_at` ve `updated_at` TTL index'i ekle (örneğin 30 gün).

**Doğrulama:**
- `admin/observability` `queue_age_seconds` < 3600 ve `attention_jobs` azalmalı.

---

## BUG-10: Dashboard ilk `GET /auth/me` 401 dönüyor

**Önem:** Düşük  
**Gözlem:** Dashboard ilk yüklendiğinde `GET /api/v1/auth/me` 401, ardından refresh + ikinci `auth/me` 200 oluyor. Console'da kırmızı 401 hatası görünüyor.

**Köken:**
- `frontend/src/lib/api.ts` içindeki `request` fonksiyonu 401 alınca refresh token isteği atayıp orijinal isteği retry ediyor. İlk istek 401 dönüyor, bu da console gürültüsüne neden oluyor.

**Düzenlenecek dosyalar:**
- `frontend/src/lib/api.ts`
- `frontend/src/components/app-shell.tsx` veya `layout.tsx`

**Çözüm:**
1. Uygulama ilk yüklendiğinde `auth/me` çağrılmadan önce sessizce `POST /api/v1/auth/refresh` çağrısı yap (access token yenileme). Access token var ve geçerliyse `auth/me` doğrudan 200 döner.
2. Alternatif olarak `api.ts` içinde 401 retry'ı gizli tut; console'a hata basmayacak şekilde `await` zincirini düzenle.

**Doğrulama:**
- Dashboard yenilendiğinde Network tab'ında `auth/me` 200 ile ilk istekte dönmeli.

---

## Genel İyileştirme Önerileri

1. **Fixture/fake provider default:** Tüm harici servisler (Instagram, AWS, Bedrock, Transcribe) için yerel geliştirme default'u fixture/fake provider olmalı. `AGENTS.md` zaten bunu öngürüyor.
2. **Hata bağlamı:** `except Exception` bloklarında `type(exc).__name__`, `str(exc)` ve hangi provider/aşamada olduğu mutlaka kaydedilmeli.
3. **Config validasyonu:** `aws_region`, `bedrock_generation_region`, `*_endpoint_url` alanları `Settings` seviyesinde validasyon ve normalization geçmeli.
4. **E2E coverage:** `frontend/e2e/` dizininde her kullanıcı ve admin akışı için en az bir spec olmalı.
5. **Kuyruk temizliği:** `cleanup_stale_jobs` periyodik task'i ekle; dashboard'da `attention_jobs` ve `stale_jobs` ayrı gösterilmeli.
