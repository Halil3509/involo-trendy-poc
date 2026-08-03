# Instagram Marka Referans Analizi — Entegrasyon Planı

> **Kapsam:** Mevcut `involo-poc-2` projesine sadece **admin** için bir “marka referans analizi” modülü eklenir. Kullanıcı bir Instagram URL/kullanıcı adı girer; sistem hedef hesabın son gönderilerini analiz edip profesyonel, chat ekranı tarzında bir markdown rapor sunar.
>
> **Yaklaşım:** PoC odaklı; mevcut altyapı (FastAPI, Celery, MongoDB, Redis, Bedrock, S3/MinIO, Next.js) maksimum düzeyde yeniden kullanılır. Dokümandaki Step Functions / Lambda / DynamoDB / API Gateway detaylarına takılmadan, projenin kendi mimarisine uyarlanır.

---

## 1. Mevcut Altyapı (Değiştirilmeden Kullanılacak)

| Bileşen | Kullanım Amacı |
|---|---|
| **FastAPI + Cookie auth + `AdminUser` dependency** | Admin-only endpoint’ler ve RBAC. |
| **Celery + Redis broker/backend** | Uzun süren analiz job’ları, cooperative iptal (`runtime.py`), lock mekanizmaları. |
| **MongoDB** | `job_runs` job durumu + yeni `brand_analysis_jobs` / `brand_analysis_posts` collection’ları. |
| **Bedrock (Nova Pro / Nova Lite)** | Görsel analiz, caption analizi ve son rapor üretimi. |
| **S3/MinIO + `MediaProvider`** | Medya indirme, keyframe çıkarma, rapor dosyası depolama. |
| **Instagram Graph API OAuth / Playwright Scraper** | Hedef hesap bilgileri ve son gönderilerin çekilmesi. |
| **Next.js 16 App Router + Tailwind CSS** | Admin chat UI ve markdown rapor görüntüleyici. |
| **WebSocket (`JobLogBus`)** | Canlı ilerleme logu (mevcut scraper-admin’dekiyle aynı mekanizma). |

---

## 2. Veri Akışı Özeti

```
Admin UI
   │ POST /api/v1/admin/brand-analysis/runs  {username_or_url}
   ▼
FastAPI ──► MongoDB job_runs insert  (state: queued)
   │
   ▼
Celery task: analyze_brand
   │
   ├── 1. Username/URL çözümle → hedef hesap metadata
   ├── 2. Son N gönderiyi çek (Graph API veya Playwright)
   ├── 3. Her post medyasını S3'e indir
   ├── 4. VisionProvider ile görsel/video kare analizi
   ├── 5. Bedrock text ile caption analizi
   ├── 6. Tüm analizleri topla → Bedrock rapor üret
   └── 7. S3'e report.md kaydet + MongoDB state: completed
   │
   ▼
Admin UI poll/WebSocket ile ilerleme + rapor GET /admin/brand-analysis/reports/{id}
   │
   ▼
Chat ekranında markdown rapor render
```

---

## 3. Fazlar

### Faz 0 — Mimari Kararlar ve Hazırlık ✅

**Durum:** Tamamlandı. Kararlar bu bölümde ve yukarıdaki "5. Karar Notları" altında güncellendi; `.env.example` için gerekli anahtarlar eklendi.

Bu fazda yalnızca kararlar alınır, **kod yazılmaz**.

- **Girdi formatı:** Admin ister `https://www.instagram.com/markaadi/` ister sadece `markaadi` yazsın, backend bunu `username`’e çözer.
- **Veri kaynağı seçimi (PoC):**
  - **Tercihli yol:** Mevcut `GraphInstagramProfileProvider` ile bağlı adminin kendi hesabına izin verdiği hedef hesap.
  - **Yedek yol:** `Playwright` public shortcode sayfalarından sınırlı metadata (caption, beğeni, permalink).
- **Gönderi limiti:** PoC’te `max_posts = 10` ile sınırlandırılır.
- **Medya türleri:** IMAGE, VIDEO/CAROUSEL_ALBUM (carousel’da ilk görünür medya alınır).
- **Rapor dili:** Türkçe.
- **LLM modeli:**
  - Görsel/video analizi → mevcut `bedrock_vision_model_id` (`eu.amazon.nova-pro-v1:0`).
  - Caption/rapor → `bedrock_generation_region` içindeki Nova Pro veya mevcut `profile_summary` provider.

**Çıktılar:** Kararlar bu dokümanda güncellenir, `.env.example` için gerekli anahtarlar listesi çıkarılır.

---

### Faz 1 — Veri Modeli + API İskeleti ✅

**Durum:** Tamamlandı.

Amaç: Analiz job’unun başlatılabileceği, durumunun sorgulanabileceği ve raporunun alınabileceği API yüzeyini oluşturmak.

#### Backend

1. **`backend/app/schemas/brand_analysis.py` oluştur**
   - `BrandAnalysisRequest` → `username_or_url: str`, `max_posts: int = 10`.
   - `BrandAnalysisJob` → `id, kind="brand_analysis", state, counters, created_at, started_at, finished_at, error, report_s3_key, report_text`.
   - `BrandAnalysisPost` → `post_id, permalink, caption, media_type, media_url, like_count, comment_count, media_s3_key, visual_analysis, caption_analysis`.
   - `BrandAnalysisReport` → `job_id, markdown_text, report_s3_key`.

2. **`backend/app/api/routes/admin_brand_analysis.py` oluştur**
   - `POST /api/v1/admin/brand-analysis/runs` → `BrandAnalysisRequest` alır, `job_runs`’a yeni kayıt atar, Celery task’i başlatır, `202 Accepted` + `BrandAnalysisJobResponse` döner.
   - `GET /api/v1/admin/brand-analysis/runs/{id}` → mevcut job durumu.
   - `GET /api/v1/admin/brand-analysis/reports/{id}` → tamamlandıysa `report_text` veya `report_s3_key`; değilse `409 Conflict`.

3. **`backend/app/workers/tasks/brand_analysis.py` oluştur**
   - `analyze_brand` Celery task’i; `execute_job` ile `runtime.py` lifecycle’ına entegre.
   - İlk hali sadece `state`’i `queued → running → succeeded` geçirir ve sahte `counters` döner.

4. **`backend/app/services/brand_analysis.py` oluştur**
   - `BrandAnalysisService` class; `run(resources)` async metodu.
   - İlk hali yalnızca log basar ve `{"fetched": 0, "analyzed": 0}` döner.

5. **`backend/app/api/application.py` güncelle**
   - Yeni router’ı `api_prefix` altına ekle: `app.include_router(brand_analysis_router, prefix=settings.api_prefix)`.

#### Frontend

6. **`frontend/src/lib/types.ts` güncelle**
   - `BrandAnalysisJob`, `BrandAnalysisReport`, `BrandAnalysisRequest` tipleri.
   - `JobKind` ve `JobState` literallerine `brand_analysis` ekleme (gerekirse).

7. **`frontend/src/lib/api.ts` güncelle**
   - `startBrandAnalysis(request)`
   - `getBrandAnalysisJob(id)`
   - `getBrandAnalysisReport(id)`

**Doğrulama:**
- `cd backend && uv run ruff check . && uv run mypy app` temiz çıkar.
- `cd frontend && npm run lint && npm run typecheck` temiz çıkar.
- Manuel: `POST /api/v1/admin/brand-analysis/runs` ile admin token üzerinden `202` döner, `GET` ile job durumu `succeeded` olur.

---

### Faz 2 — Veri Toplama (Hedef Hesap → Postlar) ✅

**Durum:** Tamamlandı. Hedef hesap URL/kullanıcı adı `BrandAnalysisProvider` ile çözümlenir, son N gönderi `brand_analysis_posts` koleksiyonuna idempotent olarak yazılır ve `JobLogBus` üzerinden canlı log akar.

Amaç: Girilen kullanıcı adından son N gönderiyi çekmek ve analiz için hazır hale getirmek.

#### Backend

8. **`backend/app/services/brand_analysis.py` → `fetch_posts()`**
   - Input `username_or_url`’den `username` çıkar.
   - Eğer mevcut `GraphInstagramProfileProvider` ile erişim varsa `fetch_recent_media` çağrılır; aksi halde `InstagramScraper` public sayfa akışı denenir.
   - Her post için minimum: `id, shortcode, caption, media_type, media_url, permalink, timestamp, like_count, comment_count`.

9. **`backend/app/providers/` genişletmesi (gerekirse)**
   - `instagram_profile.py` içinde public username → medya listesi için yeni bir yedek metod veya ayrı `brand_scraper.py`.
   - Mevcut `scraper.py`’deki `parse_instagram_url`, `_extract_post_metadata` gibi fonksiyonlar yeniden kullanılır.

10. **MongoDB `brand_analysis_jobs` collection güncelle**
    - `state` geçişleri: `queued → fetching → fetched`.
    - `target_username`, `requested_url`, `post_count` alanları.

11. **Job log entegrasyonu**
    - `BrandAnalysisService` içinde `emit` fonksiyonu ile `JobLogBus`’a adım adım log gönder.
    - Örnek: `"Hedef hesap: caudalie"`, `"10 gönderi bulundu"`.

#### Frontend

12. **`frontend/src/components/brand-analysis-chat.tsx` (ilk iskelet)**
    - URL/username input, gönder butonu.
    - Gönderildikten sonra `startBrandAnalysis` çağrılır, dönen job id tutulur.
    - Kısa polling (3 sn) ile `getBrandAnalysisJob` durumunu çeker.

**Doğrulama:**
- `pytest backend/tests/test_brand_analysis.py::test_fetch_posts` fake provider ile 10 post döner.
- Admin UI’dan istek atıldığında job `fetched` durumuna ulaşır.
- Loglar WebSocket veya sonradan `job_runs.logs` üzerinden görülebilir.

---

### Faz 3 — Medya İndirme + Görsel ve Caption Analizi ✅

Amaç: Her post için görsel/video ve caption analizini tamamlayıp `brand_analysis_posts` collection’ına kaydetmek.

#### Backend

13. **`backend/app/services/brand_analysis.py` → `process_posts()`**
    - Her post için `MediaProvider.ingest(source_url, content_id)` ile S3’e indir.
    - `content_id` formatı: `brand:{job_id}:{post_id}`.
    - İndirilen medya `StoredMedia` nesnesi elde edilir.

14. **Görsel/Video Analizi**
    - `IMAGE` → `VisionProvider.analyze(media, [], caption=...)`.
    - `VIDEO/REELS` → `MediaProvider.extract_keyframes(...)` ile keyframe listesi al, ardından `VisionProvider.analyze(media, keyframes, caption=...)`.
    - `CAROUSEL_ALBUM` → ilk çocuk medyası alınır ve IMAGE/VIDEO olarak işlenir.
    - Mevcut `VisualAnalysis` şeması kullanılır; gerekirse marka odaklı ek alanlar (`brand_identity`, `product_presentation`) `caption_analysis` JSON içinde ayrıca üretilir.

15. **Caption Analizi**
    - Yeni `BrandCaptionAnalyzer` provider/servis: Bedrock text modeline gönderilir.
    - Prompt çıktısı JSON; alanlar: `tone`, `structure`, `hashtag_strategy`, `emoji_usage`, `cta_type`, `keywords`, `target_audience_hint`, `message_clarity_score`.
    - `caption_analysis` alanına kaydedilir.

16. **MongoDB `brand_analysis_posts` collection**
    - `job_id, post_id, permalink, caption, media_type, metrics, media_s3_key, visual_analysis, caption_analysis, analyzed_at`.

17. **İlerleme logu**
    - Her post işlendiğinde `"Post {index}/{total} analiz edildi"` mesajı `JobLogBus` üzerinden gönderilir.

**Doğrulama:**
- `pytest` ile fake `VisionProvider` ve fake caption analyzer kullanılarak 10 posttan `brand_analysis_posts` collection’ı oluşur.
- `GET /api/v1/admin/brand-analysis/runs/{id}` durumu `analyzing` → `analyzed` geçişini gösterir.
- S3/MinIO’da `brand:{job_id}:*` key’leriyle medya/keyframes görülür.

---

### Faz 4 — Rapor Üretimi (Markdown) ✅

Amaç: Tüm post analizlerini birleştirip `brand_analyzer_example_output.md`’e benzeyen, Türkçe, yapılandırılmış bir markdown rapor üretmek.

#### Backend

18. **`backend/app/services/brand_analysis.py` → `generate_report()`**
    - `brand_analysis_posts` collection’ından tüm postları `job_id` ile topla.
    - Post bazında `visual_analysis`, `caption_analysis` ve metrikleri bir summary dict’ine indirge.
    - `aggregate_metrics()` fonksiyonu: ortalama beğeni, yorum, gönderi başına engagement, en yüksek/düşük performanslı postlar.

19. **Bedrock rapor promptu**
    - Sistem: `"Sen profesyonel bir Instagram marka stratejistisin. Verilen verilere dayanarak marka referans analizi raporu yaz."`
    - Kullanıcı promptu: hesap adı, takipçi (varsa), post sayısı, toplu metrikler, her postun kısa özeti.
    - Çıktı istenen bölümler:
      1. Hesap Genel Görünümü
      2. İçerik Stratejisi ve Hipotezler
      3. Görsel/Video Dili ve Ton
      4. Caption ve Hashtag Stratejisi
      5. Engagement ve Performans Değerlendirmesi
      6. Güçlü Yönler ve Gelişim Fırsatları
      7. Öneriler (5 maddede, somut)
    - Çıktı formatı: düz markdown, başlıklar ve madde listeleri.

20. **Rapor depolama**
    - Üretilen markdown `S3`’e `reports/brand/{job_id}/report.md` olarak kaydedilir.
    - `brand_analysis_jobs` dokümanına `report_s3_key` ve `report_text` (özet için ilk 2.000 karakter) yazılır.
    - `job_runs` durumu `completed` yapılır.

21. **Gömülü görsel galerisi**
    - `BrandAnalysisReportProvider` raporun sonuna `## Ek — Referans Gönderi Galerisi` bölümü ekler.
    - Her seçili post için `![açıklama](görsel_url)` markdown görseli yer alır; görsel altında shortcode, beğeni/yorum sayısı ve permalink bağlantısı bulunur.
    - İlk 5 post gösterilir; video/reels gönderilerde `thumbnail_url` (varsa) veya S3 keyframe’den üretilen presigned URL, IMAGE gönderilerde orijinal `media_url` kullanılır. PDF export bu görselleri de içerir.

22. **Rapor endpoint güncellemesi**
    - `GET /api/v1/admin/brand-analysis/reports/{id}` tamamlandığında `report_text` + `report_s3_key` döner.
    - Henüz tamamlanmadıysa `409 Conflict` + `state`.

**Doğrulama:**
- `pytest` ile fake Bedrock provider rapor üretir.
- Rapor markdown içeriği en az tüm başlık bölümlerini içerir.
- `GET /admin/brand-analysis/reports/{id}` `200` döner ve `report_text` dolu gelir.

---

### Faz 5 — Admin Chat UI ✅

Amaç: Adminin URL/username girdiği, ilerlemeyi chat baloncuklarında gördüğü ve rapora yumuşak bir geçişle ulaştığı ekranı oluşturmak.

**Durum:** Tamamlandı.

#### Frontend

22. **`frontend/src/app/admin/brand-analysis/page.tsx` oluştur**
    - `AppShell` ile sarmalanmış yeni admin sayfası.
    - `<BrandAnalysisChat />` ve `<BrandAnalysisReport />` bileşenlerini içerir.

23. **`frontend/src/components/brand-analysis-chat.tsx` oluştur**
    - State: `messages`, `input`, `jobId`, `job`, `loading`, `error`.
    - Kullanıcı mesajı gönderildiğinde mesaj listesine eklenir ve `startBrandAnalysis` çağrılır.
    - Gelen `jobId` ile her 3 saniyede `getBrandAnalysisJob` polling yapılır.
    - Her yeni `logs` ve `state` değişikliği sistem mesajı olarak chat’e eklenir:
      - `fetching` → “Gönderiler toplanıyor...”
      - `analyzed` → “Görseller ve caption’lar analiz edildi.”
      - `completed` → “Rapor hazır.”
    - Tamamlandığında `getBrandAnalysisReport` çağrılır ve `report` state’i set edilir.
    - Hata durumunda kırmızı baloncuk.

24. **`frontend/src/components/brand-analysis-report.tsx` güncelle**
    - Markdown metni alır, güvenli HTML’e çevirip render eder.
    - `![açıklama](url)` markdown görsel sözdizimini `<img>` ile render eder; `http` ve `https` protokollü URL’ler desteklenir.
    - Ayrı `MediaGallery` video oynatıcı kaldırıldı; medya kanıtları rapor markdown’ının sonuna gömülü galeri olarak sunulur.
    - Kopyala butonu, raporu tam ekran görüntüleme (opsiyonel).

25. **`frontend/src/components/app-shell.tsx` güncelle**
    - Admin link listesine `{ href: "/admin/brand-analysis", label: "Brand analysis" }` ekle.

26. **`frontend/src/lib/api.ts` güncellemeleri**
    - `startBrandAnalysis`, `getBrandAnalysisJob`, `getBrandAnalysisReport` endpoint’leri doğru yollara göre uygulanır.
    - `ApiError` ve auth cookie mekanizması mevcut yapıyla kullanılır.

#### Backend

27. (Gerekirse) CORS/preflight kontrolü — yeni route zaten `api_prefix` altında, auth cookie ile çalışır.

**Doğrulama:**
- `npm run lint && npm run typecheck && npm test -- --run && npm run build` temiz çıkar.
- E2E veya manuel: admin giriş yaptıktan sonra `/admin/brand-analysis` sayfası görünür, inputa `caudalie` yazınca job başlar ve sonunda rapor ekranda belirir.

---

### Faz 6 — Test, Doküman ve Cilalama ✅

Amaç: Kodun production kalitesinde, test edilmiş ve belgelenmiş olmasını sağlamak.

**Durum:** Tamamlandı.

#### Backend

28. **`backend/tests/test_brand_analysis.py` oluştur**
    - `test_start_job_requires_admin`
    - `test_start_job_invalid_input`
    - `test_fetch_posts_fake_provider`
    - `test_analyze_posts_fake_vision_and_caption`
    - `test_generate_report_fake_bedrock`
    - `test_get_report_before_completion_returns_409`
    - `test_stop_job_cancels_via_redis`

29. **Provider fake/fixture güncellemeleri**
    - `backend/tests/fakes.py` veya `provider_doubles.py` içine `FakeBrandCaptionAnalyzer`, `FakeBrandAnalysisReportProvider` ekle.
    - `backend/app/core/config.py`’ye `brand_analysis_provider: Literal["aws", "fake"] = "aws"` ekle (veya mevcut `vision_provider`/`embedding_provider` mantığına benzer).

30. **`.env.example` ve `backend/app/core/config.py` güncelle**
    - `brand_analysis_max_posts` (default 10)
    - `brand_analysis_report_model_id` (default `eu.amazon.nova-pro-v1:0`)
    - `brand_analysis_caption_model_id` (default `eu.amazon.nova-pro-v1:0`)
    - `brand_analysis_report_max_tokens` (default 4000)

#### Frontend

31. **`frontend/src/components/brand-analysis-chat.test.tsx` oluştur**
    - Input render ve submit.
    - API çağrısı sonrası loading durumu.
    - Hata mesajı gösterimi.
    - Tamamlanmış raporun ekrana yansıması.

32. **`frontend/src/components/brand-analysis-report.test.tsx` oluştur**
    - Markdown içeriği doğru render edilir.
    - Kopyala butonu çalışır.

33. **README / Proje dokümanları**
    - `docs/PROJECT_ARCHITECTURE.md`’e Brand Analysis modülü ekle.
    - `README.md`’ye admin yeteneği listesine ekle.
    - `docs/brand-analysis-integration-plan.mmd` korunur (Mermaid görsel özet).

#### Final Doğrulama

34. **Tam kapı (full gate)**
    ```bash
    cd backend && uv run ruff check . && uv run mypy app && uv run pytest
    cd ../frontend && npm run lint && npm run typecheck && npm test -- --run && npm run build
    ```
    veya
    ```bash
    make verify
    ```

---

## 4. Risks ve Açık Sorular

| Risk | Etki | Çözüm / Mitigasyon |
|---|---|---|
| **Meta Graph API erişimi** | Hedef hesap public veya adminin bağlı hesabı olmalı; başka hesaplar için izin gerekir. | PoC’te Playwright public shortcode yedek yol; `brand_analysis_provider=fake` ile test edilebilir. |
| **Bedrock maliyeti** | Her post için vision + caption + rapor çağrısı maliyetli. | `max_posts=10` sınırı; vision keyframe’leri sadece 4 frame; PoC’te `vision_provider=fake` seçilebilir. |
| **Video transcript** | Reels ses analizi maliyetli ve uzun. | PoC’te transcript zorunlu değil; sadece caption + keyframe yeterli. İleride `TranscribeProvider` entegre edilir. |
| **Markdown render güvenliği** | XSS riski. | PoC’te `<pre>` düz metin; zengin render için `react-markdown` + `rehype-sanitize` kullanılır. |
| **İptal/stop** | Uzun analizlerde kullanıcı durdurmak isteyebilir. | Mevcut `POST /api/v1/admin/jobs/{id}/stop` + `runtime.py` Redis cancel kullanılır. |
| **Qdrant bağımlılığı** | Marka analizi vektör depolamaya ihtiyaç duymaz. | Bu PoC’te Qdrant’a yazma yapılmaz; sadece MongoDB + S3 kullanılır. |

---

## 5. Karar Notları

- **Teknoloji:** Mevcut `FastAPI + Celery + MongoDB + Redis + S3 + Bedrock + Next.js` korunur; yeni servis (Lambda, Step Functions, DynamoDB) eklenmez.
- **Depolama:** `brand_analysis_jobs` ve `brand_analysis_posts` collection’ları MongoDB’de açılır; rapor dosyası S3’te saklanır.
- **Auth:** Sadece `AdminUser` dependency’si ile korunur; frontend cookie tabanlı auth zaten mevcut.
- **Canlı izleme:** WebSocket (`JobLogBus`) veya 3 sn polling kullanılabilir; PoC’te polling daha basit ve mevcut `scraper-admin.tsx` örnek alınarak uygulanır.
- **Dil:** Rapor Türkçe; kullanıcı arayüzü mevcut İngilizce admin UI’sine Türkçe/İngilizce karışık eklenebilir (PoC’ta mevcut İngilizce etiketler korunur, rapor içeriği Türkçe).

---

## 6. Uygulama Tamamlandı

Faz 5 (Admin Chat UI) ve Faz 6 (test, doküman ve cilalama) uygulandı. Değişiklikler:

- `frontend/src/app/admin/brand-analysis/page.tsx` düzeni `page-container` ile sarmalandı.
- `frontend/src/components/brand-analysis-chat.tsx` stilleri `button`/`button-primary`/`button-secondary` olarak düzeltildi, `analyzed` durumu eklendi, rapor yükleme `succeeded`/`analyzed` için genişletildi ve rapor ayrı `brand-analysis-report.tsx` bileşenine çıkarıldı.
- `frontend/src/components/brand-analysis-report.tsx` oluşturuldu; markdown `<pre>` içinde ve kopyala butonu ile gösterir.
- `frontend/src/components/brand-analysis-chat.test.tsx` ve `brand-analysis-report.test.tsx` eklendi.
- `backend/tests/test_brand_analysis.py` genişletildi: admin yetkisi, geçersiz input, fake provider fetch ve brand-analysis job iptali testleri.
- `README.md` ve `docs/PROJECT_ARCHITECTURE.md` güncellendi.

Son doğrulama: `make verify` hedefiyle tam kapı çalıştırılacak.
