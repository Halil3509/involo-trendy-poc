# Involo POC — Manuel / E2E Test Raporu

> **Tarih:** 2026-07-31  
> **Amaç:** `run-local.sh` ile yerel ortamı çalıştırarak, MCP Playwright ve API çağrılarıyla uçtan uca akışları denemek, karşılaşılan hataları ve iyileştirme alanlarını raporlamak.  
> **Test ortamı:** Linux, backend `http://localhost:8021`, frontend `http://localhost:8020`, altyapı (MongoDB 8022, Redis 8028, Qdrant 8024, MinIO 8026) Docker'da çalışıyor.

---

## 1. Özet

Sistem **temel kimlik doğrulama, admin yapılandırma, brand-analysis raporlama ve sağlık endpointleri** açısından çalışır durumda. Ancak **Instagram/Playwright tabanlı scrape, creator tracking, transcript ve bazı Bedrock (eu.* model) entegrasyonları** ciddi hatalarla karşılaşıyor. Yerel testlerde `run-local.sh` başlangıcı `localtunnel` sorunundan dolayı ilk denemede çöktü; `INVOLO_NGROK_DOMAIN` kapatıldıktan sonra düzgün ayağa kalktı.

**Kritik bulgular:**

- `INVOLO_NGROK_DOMAIN=auto` + `run-local.sh` = localtunnel rezervasyonu başarısız oluyor.
- Playwright MCP sunucusu navigasyon sırasında kapandı; UI otomasyonu yarım kaldı.
- Trend content `failed` durumunda; `enrichment_error: "Invalid endpoint: "` hatası var.
- AWS Bedrock `eu.amazon.nova-pro-v1:0` ve `eu.amazon.nova-lite-v1:0` modelleri `%100` hata oranına sahip.
- `aws-transcribe-identify-language` aşaması ~%39 başarısız (`70/179`.
- `creator_track` ve `scrape` işleri Instagram rate limit (429), tarayıcı bağlamı kapanması ve Instagram checkpoint/challenge nedeniyle `needs_intervention`/`failed` oluyor.
- Yeni creator ekleme (`POST /api/v1/creators`) var olmayan kullanıcı adını da kabul ediyor; sonrasında tracking başarısız.
- `queue_age_seconds` 93.353 saniye (yaklaşık 26 saat) ve `attention_jobs` 31 adet; eski kuyruk işleri temizlenmemiş.

---

## 2. Test Ortamı ve Yöntem

1. **Altyapı:** `make infra-up` + `minio-init` zaten çalışıyordu.
2. **Uygulama:** `./run-local.sh up` ile ayağa kaldırıldı.
   - API: `http://localhost:8021`
   - UI: `http://localhost:8020`
3. **UI otomasyonu:** MCP Playwright (`mcp3_*`) ile giriş yapılıp Dashboard, Profile ve Creators sayfaları gezildi. Navigasyon Creators detaya tıklanırken MCP transport kapandı.
4. **API otomasyonu:** `curl` ile çerez bazlı oturum kullanılarak uç noktalar test edildi.
5. **Veri kaynağı:** API yanıtları, `job_runs` koleksiyonu, `admin/observability`, `admin/trend-content` ve `brand_analysis_*` koleksiyonları.

---

## 3. Smoke Test Sonuçları

| Test | Durum | Notlar |
|------|-------|--------|
| `GET /health/live` | ✅ | `{"status":"ok"}` |
| `GET /health/ready` | ✅ | Mongo, Redis, Qdrant, S3, Meta token, Bedrock hepsi `ok` |
| `GET /docs` (Swagger) | ✅ | Swagger UI yükleniyor |
| `GET http://localhost:8020` | ✅ | 200, login/dashboard yönlendirmesi çalışıyor |
| MinIO sağlık | ✅ | `200` dönüyor |

---

## 4. UI / Kullanıcı Akışları (MCP Playwright)

| Bölüm | Durum | Notlar |
|-------|-------|--------|
| Login/Logout | ✅ | Admin hesabıyla giriş/çıkış yapıldı; cookie yenileme çalıştı |
| Dashboard | ✅ | Kullanıcı bilgisi, admin linkleri, "Generate 3 ideas" disabled (Instagram bağlı değil) doğru görünüyor |
| Profile | ✅ | "Not connected" mesajı, boş analytics ve recommendation history doğru |
| Creators list | ✅ | Var olan `majasrecipes` kaydı görünüyor; detaya tıklanırken MCP transport kapandı |

**Playwright notu:** `http://localhost:8020/creators/{id}` yüklenirken MCP Playwright tarafı "transport closed" hatası verdi. Sonraki `mcp3_*` çağrıları da aynı hatayı verdi; muhtemelen Playwright MCP sunucusu çöktü veya IDE bağlantısı koptu. Bundan sonraki UI testleri `curl` ile API seviyesinde devam etti.

---

## 5. API Uç Nokta Testleri

### 5.1 Kimlik doğrulama ve kullanıcı

| Endpoint | Durum | Notlar |
|----------|-------|--------|
| `POST /api/v1/auth/login` | ✅ | 200, kullanıcı ve rol dönüyor |
| `GET /api/v1/auth/me` | ✅ | 200 |
| `GET /api/v1/preferences` | ✅ | 200 |
| `PUT /api/v1/preferences` | ✅ | 200, güncelleme yansıyor |
| `POST /api/v1/auth/register` (mevcut e-posta) | ✅ | 409/400 "Email already registered" |

### 5.2 Instagram / öneriler

| Endpoint | Durum | Notlar |
|----------|-------|--------|
| `GET /api/v1/instagram/status` | ✅ | `disconnected` döndü |
| `POST /api/v1/instagram/oauth/start` | ✅ | Authorization URL oluşturdu |
| `POST /api/v1/profile/sync` | ✅ | 409: "Instagram account is not connected" — beklenen davranış |
| `GET /api/v1/recommendations?limit=10` | ✅ | Boş dizi döndü |
| `POST /api/v1/recommendations` | ✅ | 409: "Instagram profile must be connected and ready..." — beklenen davranış |

### 5.3 Creators

| Endpoint | Durum | Notlar |
|----------|-------|--------|
| `GET /api/v1/creators` | ✅ | 200, `majasrecipes` kaydı var |
| `GET /api/v1/creators/{id}` | ✅ | 200, tüm alanlar `0`/`null` |
| `GET /api/v1/creators/{id}/followers` | ✅ | 200, boş `points` |
| `GET /api/v1/creators/{id}/content` | ✅ | 200, boş `items` |
| `POST /api/v1/creators` | ⚠️ | `not_a_real_user_xyz` kabul edildi; varlık doğrulaması yok |

### 5.4 Admin — scraper / pipeline

| Endpoint | Durum | Notlar |
|----------|-------|--------|
| `GET /api/v1/admin/overview` | ✅ | 200, metrikleri dönüyor |
| `GET /api/v1/admin/observability` | ✅ | 200, provider usage ve threshold'lar dönüyor |
| `GET /api/v1/admin/jobs` | ✅ | 200, filtreleme çalışıyor |
| `GET /api/v1/admin/scraper/config` | ✅ | 200 |
| `PUT /api/v1/admin/scraper/config` (geçersiz cron) | ✅ | 422 "schedule_cron must be a valid cron expression" |
| `PUT /api/v1/admin/scraper/config` (geçerli) | ✅ | 200, kaydedildi |
| `POST /api/v1/admin/scraper/runs` (boş keywords) | ✅ | 422 "At least one keyword is required" |
| `POST /api/v1/admin/scraper/runs` (keyword=travel) | ⚠️ | 202 ile kuyruğa alındı; işlem sonrası `needs_intervention` (Instagram checkpoint) |

### 5.5 Admin — profiling

| Endpoint | Durum | Notlar |
|----------|-------|--------|
| `GET /api/v1/admin/profiling/config` | ✅ | 200 |
| `PUT /api/v1/admin/profiling/config` (geçersiz cron) | ✅ | 422 |
| `PUT /api/v1/admin/profiling/config` (geçerli) | ✅ | 200, kaydedildi |

### 5.6 Admin — brand analysis

| Endpoint | Durum | Notlar |
|----------|-------|--------|
| `POST /api/v1/admin/brand-analysis/runs` (eksik payload) | ✅ | 422 validation |
| `POST /api/v1/admin/brand-analysis/runs` (max_posts > 30) | ✅ | 422 validation |
| `GET /api/v1/admin/brand-analysis/runs/{id}` | ✅ | 200, `caudalie` işi `succeeded` |
| `GET /api/v1/admin/brand-analysis/runs/{id}/posts` | ✅ | 200, medya ve analiz verisi dolu |
| `GET /api/v1/admin/brand-analysis/reports/{id}` | ✅ | 200, markdown rapor döndü |
| `GET /api/v1/admin/brand-analysis/reports/{id}/pdf` | ✅ | 200, `application/pdf`, ~1 MB dosya indirildi |

### 5.7 Admin — trend content

| Endpoint | Durum | Notlar |
|----------|-------|--------|
| `GET /api/v1/admin/trend-content?limit=5` | ✅ | 200, 165 kayıt var |
| `GET /api/v1/admin/trend-content/6a6ae713.../` | ⚠️ | 307 redirect (trailing slash) |
| `GET /api/v1/admin/trend-content/6a6ae713...` | ⚠️ | 200 ama `processing_status: failed`, `enrichment_error: "Invalid endpoint: "` |

---

## 6. Bulgular ve Hatalar

### BUG-1: `run-local.sh` `localtunnel` başarısızlığı

**Önem:** Yüksek  
**Açıklama:** `.env` içinde `INVOLO_NGROK_DOMAIN=auto` varsa `run-local.sh` `localtunnel --port 8021` çalıştırıyor. Ağ/otelnet sorunlarında 3 deneme sonrası script `exit 1` ile kapanıyor.  
**Etki:** Sistem hiç başlamıyor.  
**Geçici çözüm:** `.env` içinden `INVOLO_NGROK_DOMAIN` satırı kapatılarak yerel çalıştırma yapıldı.  
**Öneri:** `run-local.sh` için `INVOLO_NGROK_DOMAIN=auto` durumunda localtunnel başarısız olursa `localhost` moduna graceful fallback yapılsın.

### BUG-2: MCP Playwright transport kopması

**Önem:** Orta  
**Açıklama:** `mcp3_browser_click` Creators detay linkine tıklarken "transport closed" hatası verdi; sonraki tüm `mcp3_*` çağrıları da başarısız oldu.  
**Etki:** UI otomasyonu yarım kaldı.  
**Öneri:** MCP Playwright sunucusunun yeniden başlatılması; alternatif olarak `frontend/e2e/` Playwright spec'leri `npm run test:e2e` ile çalıştırılabilir.

### BUG-3: Trend content zenginleştirme hatası (`Invalid endpoint`)

**Önem:** Yüksek  
**Açıklama:** `/admin/trend-content` sorgusunda `processing_status: failed` olan kayıtların `enrichment_error` alanı `"Invalid endpoint: "` olarak dolu.  
**Etki:** Scrape edilen 165 kayıttan büyük bölümü zenginleştirilemiyor; embedding/recommendation zinciri kırılıyor.  
**Örnek:** `shortcode: DaT_-SLTp1B`, `discovered_keywords: ["travel"]`, `enrichment_error: "Invalid endpoint: "`  
**Öneri:** `enrich_trend_content` task'inde kullanılan transcribe/vision/embed provider uç noktaları ve `boto3`/`httpx` istemci yapılandırması kontrol edilsin; hata mesajları daha açıklayıcı hale getirilsin.

### BUG-4: `eu.*` Bedrock modelleri %100 hata

**Önem:** Yüksek  
**Açıklama:** `admin/observability` verisinde `eu.amazon.nova-pro-v1:0` (brand_caption 15/15, brand_report 2/2) ve `eu.amazon.nova-lite-v1:0` (brand_caption 20/20) tamamen başarısız. `us.amazon.nova-pro-v1:0` ve `us.amazon.nova-lite-v1:0` aynı aşamalarda başarılı.  
**Etki:** Brand caption/report aşamaları cross-region model ID yanlışlığından bozuk.  
**Öneri:** `INVOLO_BRAND_ANALYSIS_*_MODEL_ID` değerleri `us.` prefixli veya kullanılan bölgeye (`us-east-1`) uygun ARN/model ID ile güncellensin.

### BUG-5: Transcribe `aws-transcribe-identify-language` yüksek hata oranı

**Önem:** Orta  
**Açıklama:** `aws_transcribe` `aws-transcribe-identify-language` aşamasında 179 çalışmadan 70'i hatalı (`%39` hata).  
**Etki:** Video transkripsiyonu eksik kalıyor; trend score ve embedding kalitesi düşüyor.  
**Öneri:** Ses dosyası format/boyut limitleri, `Invalid endpoint` hatası (BUG-3) ile ilişkili olabilir; log ve exception detayları incelensin.

### BUG-6: Instagram tabanlı scrape/creator tracking çökmeleri

**Önem:** Yüksek  
**Açıklama:**
- `scrape` işi: `BrowserType.launch_persistent_context: Executable doesn't exist at .../chrome-linux64/chrome` (Chromium kurulmadan önceki tarihli).
- Yeni başlatılan `scrape` işi: `Instagram login was not accepted; page message: checkpoint`.
- `creator_track`: `Instagram profile API returned 429`, `upstream returned 429`, `BrowserContext.close: ...`, `Meta token exchange failed ... OAuthException code 101`.

**Etki:** Scrape ve creator tracking gerçek Instagram ortamında çalışmıyor.  
**Öneri:**
- CI/yerel testler için `INVOLO_SCRAPER_PROVIDER` / `INVOLO_CREATOR_TRACKING_PROVIDER` = `fixture` veya `fake` seçeneği sağlanmalı.
- Rate limit ve checkpoint durumları için `needs_intervention` akışı zaten var, ancak `429`'lar tekrarlanıyor; retry/backoff ve eksik token durumu netleştirilmeli.

### BUG-7: Creator oluşturma var olmayan kullanıcı adını kabul ediyor

**Önem:** Orta  
**Açıklama:** `POST /api/v1/creators` ile `not_a_real_user_xyz` başarıyla kaydedildi (`status: active`).  
**Etki:** Var olmayan hesaplar için tracking işleri boşa kuyruğa giriyor.  
**Öneri:** Kullanıcı adı eklemeden önce Instagram Graph API veya Playwright ile varlık kontrolü yapılsın; yoksa 422 dönülsün.

### BUG-8: `/api/v1/admin/trend-content/{id}/` trailing slash 307 yapıyor

**Önem:** Düşük  
**Açıklama:** Detay uç noktası trailing slash ile 307 Temporary Redirect veriyor. Frontend veya API çağrıları bu yüzden ek network turu atıyor.  
**Öneri:** FastAPI router'da `/{id}` ve `/{id}/` ikisini de aynı handler'a yönlendirin veya frontend'de trailing slash kullanılmasın.

### BUG-9: `admin/observability` `queue_age_seconds` çok yüksek

**Önem:** Orta  
**Açıklama:** `queue_age_seconds: 93353` (~26 saat), `stale_trends: 50`, `attention_jobs: 31`. Eski işler hâlâ kuyrukta/kuyruk izleniminde görünüyor.  
**Etki:** Operasyonel dashboard yanıltıcı; takip edilmesi gereken gerçek sorunlar gizlenebilir.  
**Öneri:** `scheduled_dispatch` veya Celery beat tarafında eski `queued` kayıtlarının zaman aşımına uğratılması/güncellenmesi; dashboardda "stale" ayrımı yapılsın.

### BUG-10: Dashboard her yüklendiğinde `401 Unauthorized` console hatası

**Önem:** Düşük  
**Açıklama:** `GET /api/v1/auth/me` ilk çağrı 401 dönüyor, ardından refresh 200 ve ikinci `auth/me` 200 oluyor.  
**Etki:** Kullanıcıya görünür etki yok ama console'da kırmızı hata var; a11y/izleme aracı bildirimleri yapabilir.  
**Öneri:** Erişim token'ı süresi dolmuşsa `auth/me` çağrısı yapılmadan önce sessiz refresh isteği gönderilsin veya 401'den önce `refresh` tercihen çağrılsın.

---

## 7. İyileştirme Önerileri

1. **Yerel geliştirme kolaylığı:** `run-local.sh` için `localtunnel` başarısız olursa localhost'a otomatik fallback; ayrıca `.env.example`'da `INVOLO_NGROK_DOMAIN` boş/comment bırakılmalı ve README'de belirtilmeli.
2. **Sahte/fixture provider'ları varsayılan yap:** Local/test ortamında Instagram, AWS, Bedrock, Transcribe yerine sahte/fikstür provider'lar çalışsın. `AGENTS.md` zaten bu prensibi vurguluyor.
3. **Model ID düzeltmesi:** `eu.*` model ID'leri `us.*` veya doğru bölge ARN'leri ile değiştirin; ortama göre validasyon ekleyin.
4. **Hata mesajlarını zenginleştir:** `Invalid endpoint` gibi çıktılar provider adı, servis ve HTTP durumu içerecek şekilde genişletilsin.
5. **Creator doğrulama:** Creator ekleme endpoint'inde önce varlık/erişilebilirlik kontrolü yapılsın.
6. **Kuyruk temizliği:** Eski `queued`/`failed` işler için TTL veya idempotent cleanup task ekleyin.
7. **E2E test coverage:** MCP Playwright dışında `frontend/e2e/` spec'leri düzenli çalıştırın; UI'da her akış için en az bir spec olsun.
8. **Trailing slash routing:** `/api/v1/admin/trend-content/{id}/` 307 yerine 200 dönsün.
9. **Auth 401 gürültüsü azaltma:** Access token'ı yenileme stratejisi frontend `api.ts`'te optimize edilsin.
10. **Snapshot coverage artırma:** `snapshot_coverage: 0.345` çok düşük; BUG-3 ve BUG-4 çözüldükten sonra re-run pipeline ile yeniden zenginleştirme yapılmalı.

---

## 8. Sonuç

**Çalışan alanlar:**
- Kimlik doğrulama, tercih yönetimi, admin yetkilendirme.
- Admin overview, observability, job list/filtreleme.
- Scraper/profiling/brand-analysis config validasyonu ve job başlatma.
- Brand analysis rapor ve PDF export (daha önce başarılı `caudalie` işi).
- Sağlık endpointleri ve altyapı hizmetleri.

**Sıkıntılı alanlar:**
- Instagram/Playwright tabanlı scrape ve creator tracking (checkpoint, rate limit, eksik/yanlış token, Chromium kurulumu).
- Trend content zenginleştirme (`Invalid endpoint` hatası).
- Bedrock `eu.*` model kullanımı.
- AWS Transcribe yüksek hata oranı.
- Yerel geliştirme ayağa kaldırma deneyimi (`localtunnel` bağımlılığı).

**Önerilen sıradaki adımlar:**
1. BUG-1 (run-local fallback) ve BUG-4 (model ID) hemen çözülmeli.
2. BUG-3 (`Invalid endpoint`) için backend logları ve provider yapılandırması incelenmeli.
3. Yerel testler için `fixture`/`fake` provider seti kurulup `make verify` çalıştırılmalı.
4. UI E2E testleri `npm run test:e2e` ile tekrar çalıştırılmalı.

---

## Ekler

- **Screenshot:** `01_dashboard_initial.png` (çalışma alanı kökünde) ilk dashboard görünümünü gösterir.
- **cookies.txt:** API testleri sırasında oluşturulan oturum çerezleri; silinmelidir.
- **/tmp/brand_report.pdf:** `caudalie` brand analysis PDF örneği.
