# Instagram Trend Analizi & AI Destekli İçerik Öneri Sistemi — Teknik Ürün Dokümanı

## 0. Doküman Amacı ve Kullanım Şekli

Bu doküman, projeyi baştan sona uygulayacak bir yapay zeka geliştirme ajanı (AI coding agent) için hazırlanmıştır. Amaç; sistemin tüm bileşenlerini, veri akışını, veri modellerini ve geliştirme fazlarını, hiçbir detay atlanmadan tarif etmektir. Doküman üç ana modülden oluşur:

1. **Instagram Scraping & Trend Veri Toplama Motoru** (Admin tarafından yönetilir, sürekli/otonom çalışır)
2. **Kullanıcı Profilleme Motoru** (Kullanıcının kendi Instagram hesabından öğrenme)
3. **AI İçerik Öneri Motoru** (Kullanıcıya özel içerik fikirleri üretimi)

Bu üç modülün altında ortak bir **veri işleme pipeline'ı** (scrape → metadata → transcript/caption → skorlama → embedding → kümeleme → depolama) bulunur ve iki farklı bağlamda (genel trend verisi ve kullanıcı verisi) tekrar kullanılır.

> **Not:** Aşağıdaki bölümlerde geçen "AI Mühendisi Notu" kutucukları, dokümanı hazırlayan tarafın (Claude) teknik önerilerini içerir. Bunlar zorunlu değildir, ama önerilir; geliştirme ajanı bu önerileri uygulayıp uygulamamakta serbesttir, ancak uyguluyorsa dokümana not düşmelidir.

---

## 0.1 Uygulama Durumu (Implementation Status)

> Bu bölüm, kod tabanının mevcut durumunu yansıtır. **Faz 0-8 tamamlanmıştır.**
> Çalışan sistemin güncel ve operasyonel açıklaması için
> `docs/PROJECT_ARCHITECTURE.md` esas alınmalıdır.

| Faz | Durum | Kapsam |
|---|---|---|
| Faz 0 — İskelet & Altyapı | **Tamamlandı** | FastAPI katmanlı yapı, MongoDB/Qdrant/Redis bağlantıları, Docker Compose |
| Faz 1 — Auth & UI İskeleti | **Tamamlandı** | JWT (access + rotating refresh, HttpOnly cookie), RBAC, Next.js login/register/dashboard |
| Faz 2 — Scraping Botu (MVP) | **Tamamlandı** | Playwright + kalıcı session, keyword→Reels keşfi, ham `trend_content` kaydı, admin ekranı |
| Faz 3 — Metadata, Skor, Transcript | **Tamamlandı** | Metadata çekimi, viral skor, AWS Transcribe, threshold + maliyet ön filtresi |
| Faz 4 — Embedding & Kümeleme | **Tamamlandı** | Bedrock/Titan embedding, ağırlıklı vektör, HDBSCAN/K-Means, Qdrant, cron zamanlama |
| Faz 5 — Kullanıcı Profilleme | **Tamamlandı** | Graph OAuth, kullanıcı pipeline'ı, profil özeti, cron + ETA |
| Faz 6 — İçerik Öneri Motoru | **Tamamlandı** | Semantic retrieval + viral rerank, Bedrock/fake üretim, prompt cache, tekrar önleme, kartlar + geçmiş |
| Faz 7 — UI Cilalama | **Tamamlandı** | Admin overview, profil analizi, scraper canlı WebSocket log ekranı ve bileşen testleri |
| Faz 8 — Sağlamlaştırma & Test | **Tamamlandı** | Retry/backoff, Redis rate limit, güvenlik header'ları, healthcheck'ler ve genişletilmiş testler |

**Proje yapısı:** `backend/` (FastAPI + Celery, Python 3.12), `frontend/` (Next.js App Router + TypeScript + Tailwind), kök `docker-compose.yml` (mongodb, redis, qdrant, api, worker, beat, frontend).

**Sağlayıcı (provider) mimarisi — mühendislik kararı:** Her dış servis için "gerçeğe hazır gerçek entegrasyon + credential yoksa deterministik fixture/fake" ikilisi kuruldu ve config ile seçiliyor. Böylece tüm pipeline, harici anahtar olmadan uçtan uca çalışır ve test edilir. Gerçek servisler opt-in smoke testleriyle doğrulanır.

| Concern | Fixture/Fake (varsayılan) | Gerçek | Config anahtarı |
|---|---|---|---|
| Scraper | `fixture` | `instagram` (Playwright) | `INVOLO_SCRAPER_ADAPTER` |
| Metadata | Meta discovery payload | Meta discovery payload | (always Meta) |
| Transcript | `fake` | `aws` (ffmpeg + S3 + Transcribe) | `INVOLO_TRANSCRIPTION_PROVIDER` |
| Embedding | `fake` | `bedrock` (Titan) | `INVOLO_EMBEDDING_PROVIDER` |

**İçerik yaşam döngüsü (`trend_content.processing_status`):** `discovered → enriched | stored → embedded → clustered`; hata yolları `failed` / `needs_intervention`.

---

## 1. Genel Mimari Özeti

```
┌─────────────────────────────────────────────────────────────────────┐
│                          ADMIN PANELİ                                 │
│  - Scraping bot konfigürasyonu (keyword, limit, headless, zamanlama)  │
│  - Bot canlı izleme (headless / headful toggle)                       │
│  - Profiling job zamanlama + tahmini süre gösterimi                   │
│  - Cluster / eşik (threshold) değerleri yönetimi                      │
└─────────────────────────────────────────────────────────────────────┘
                │                                   │
                ▼                                   ▼
┌───────────────────────────┐        ┌───────────────────────────────┐
│  MODÜL 1: TREND SCRAPER    │        │  MODÜL 2: USER PROFILING       │
│  Playwright bot → Explore  │        │  Instagram Graph API (OAuth)   │
│  → keyword arama → Reels   │        │  → son 30 gün içerik           │
└───────────────────────────┘        └───────────────────────────────┘
                │                                   │
                └───────────────┬───────────────────┘
                                 ▼
                 ┌───────────────────────────────┐
                 │   ORTAK İŞLEME PIPELINE'I       │
                 │  1. Instagram Public API meta   │
                 │  2. Engagement/Viral skor        │
                 │  3. Ses çıkarma → AWS Transcribe │
                 │  4. Caption + transcript birleşim│
                 │  5. Text embedding                │
                 │  6. Embedding × viral skor         │
                 │  7. Kümeleme (K-Means/HDBSCAN)     │
                 └───────────────────────────────┘
                                 │
                     ┌───────────┴────────────┐
                     ▼                         ▼
              ┌─────────────┐          ┌──────────────┐
              │   Qdrant     │          │   MongoDB     │
              │ (vektörler,  │          │ (ham veri,    │
              │  cluster id) │          │  skorlar,     │
              │              │          │  user profile)│
              └─────────────┘          └──────────────┘
                     │                         │
                     └───────────┬─────────────┘
                                 ▼
                 ┌───────────────────────────────┐
                 │  MODÜL 3: İÇERİK ÖNERİ MOTORU    │
                 │  User avg vektör → Qdrant top-K  │
                 │  → transcript+skor context        │
                 │  → user profile context           │
                 │  → Amazon Bedrock (prompt cache)  │
                 │  → kart formatında içerik fikri    │
                 └───────────────────────────────┘
```

---

## 2. Teknoloji Yığını (Tech Stack)

| Katman | Teknoloji |
|---|---|
| Backend Framework | **FastAPI** (Python) |
| Veritabanı (doküman/ham veri) | **MongoDB** |
| Vektör Veritabanı | **Qdrant** |
| Web Otomasyonu / Scraping | **Playwright** (Python) |
| Konuşma → Metin | **AWS Transcribe** |
| LLM (profil çıkarımı + içerik üretimi) | **Amazon Bedrock** |
| Instagram Resmi Entegrasyon (kullanıcı hesabı bağlama) | **Instagram Graph API** (Meta Business login/verified) |
| Instagram Public İçerik Metadata | Instagram'ın public/iç API uç noktaları (scraping bağlamında) |
| Text Embedding | Bedrock üzerinden embedding modeli (örn. Titan Embeddings) veya alternatif |
| Kümeleme | **K-Means** veya **HDBSCAN** (aşağıda karşılaştırma var) |
| Job Scheduling | APScheduler / Celery + Redis (öneri, aşağıda gerekçeli) |

### AI Mühendisi Notu — Scheduling
Kullanıcı, zamanlanmış görevler (örn. "her gün saat 05:00'te scraping çalışsın") ve tahmini bitiş süresi hesaplaması istiyor. Bunun için **Celery + Redis (veya AWS SQS) tabanlı bir worker/queue mimarisi** öneriyorum; FastAPI sadece job'ı tetikler/yönetir, gerçek iş worker süreçlerinde yürütülür. Süre tahmini için basit bir hareketli ortalama (son N job'ın user-başına ortalama süresi × toplam user sayısı) yeterlidir; karmaşık bir tahmin modeline gerek yoktur.

> **Uygulandı (Faz 4):** **Celery + Redis** worker'ı kuruldu; FastAPI yalnızca job'ı tetikliyor, iş worker'da yürütülüyor. Zamanlama **DB-tabanlı** yapıldı: `schedule_cron` (5 alanlı, UTC) admin panelinden düzenlenir; **Celery beat** her dakika tetiklenip `croniter` ile geçmiş cron dilimini kontrol eder ve dolan dilim için görevi kuyruğa alır (worker yeniden başlatmaya gerek yok). Aynı anda tek scrape işi için scraper lock, tek pipeline işi (enrich/embed/cluster) için ayrı bir Redis lock kullanılır. Tahmini süre hesaplaması Faz 5 kullanıcı profilleme job'ıyla birlikte gelecektir.

---

## 3. MODÜL 1 — Instagram Trend Scraping Motoru

### 3.1 Bot Kimlik Doğrulama
- Playwright tabanlı bir bot, sabit bir Instagram hesabına (kendi hesabımıza ait username + şifre) ait **session/cookie'yi** kalıcı olarak saklar (storage state dosyası veya şifrelenmiş DB kaydı).
- Bot her çalıştığında önce saklanan session ile giriş yapmayı dener; session geçersizse yeniden login akışını (2FA dahil, gerekirse manuel müdahale bildirim mekanizması ile) çalıştırır.
- **Session'lar asla her seferinde sıfırdan login yapılarak yenilenmemeli** — Instagram'ın bot tespit mekanizmalarını tetiklememek için.

### 3.2 Keşfet / Arama Akışı
Adım adım bot davranışı:
1. Bot Instagram arayüzünde **Search (Keşfet/Arama)** sekmesine gider.
2. Admin panelinden tanımlanan **keyword listesi** üzerinden sırayla arama yapar (örn: `food`, `travel`, `lifestyle`).
3. Her keyword için **Reels** sekmesine/filtresine geçer.
4. Admin tarafından tanımlı bir **limit** kadar reel toplar (örn: keyword başına 50 reels).
5. Toplanan her reel için **link/shortcode** kaydedilir; bir sonraki adımda metadata çekimi bu shortcode üzerinden yapılır.

### 3.3 Metadata Çekimi (Instagram Public API)
- Toplanan her reels için Instagram'ın public/iç API'si üzerinden içerik metadata'sı çekilir. Minimum alınması gereken alanlar:
  - `owner_username`, `owner_follower_count`
  - `like_count`, `comment_count`, `view_count` (play_count)
  - `video_duration`
  - `caption_text`
  - `taken_at` (paylaşım tarihi)
  - `video_url` (ses çıkarma için)
  - `shortcode` / `media_id`

### 3.4 Engagement / Viral Skor Hesaplama
Skor, hesabın takipçi sayısı, beğeni, yorum ve izlenme sayısına göre normalize edilmiş bir matematiksel metriktir. Önerilen temel formül (geliştirme ajanı revize edebilir):

```
engagement_rate = (like_count + comment_count * W_comment + share_count * W_share) / max(view_count, 1)
follower_adjustment = view_count / max(follower_count, 1)
viral_score = f(engagement_rate, follower_adjustment, recency_decay)
```

- `recency_decay`: içerik ne kadar yeni ise skor o kadar güncel kalsın (zamanla ağırlığı azaltan bir üstel decay fonksiyonu önerilir).
- Skor **0-100 arası normalize edilmiş** bir değer olarak saklanmalı; ham bileşenler de (like, comment, view, follower) ayrıca DB'de tutulmalı (kullanıcının istediği gibi, skor ayrı bir alan olarak saklanacak).

### 3.5 Ses Çıkarma ve Transcript
1. `video_url` üzerinden video indirilir (geçici depolama, işlem sonrası silinir).
2. Videodan ses track'i (ffmpeg ile) ayrıştırılır.
3. Ses, **AWS Transcribe**'a gönderilir; dil otomatik algılama (`IdentifyLanguage: true`) ile transcript metni elde edilir.
4. Transcript + `caption_text` birlikte kaydedilir.

### 3.6 Veri Modeli — Ham İçerik Kaydı (MongoDB)

```json
{
  "_id": "ObjectId",
  "source": "trend_scraper",        // veya "user_profiling"
  "shortcode": "string",
  "owner_username": "string",
  "keyword": "food",                 // hangi arama ile bulunduğu
  "caption": "string",
  "transcript": "string",
  "language": "tr",
  "duration_seconds": 34,
  "metrics": {
    "like_count": 12000,
    "comment_count": 340,
    "view_count": 500000,
    "follower_count": 25000
  },
  "viral_score": 87.4,
  "embedding_vector_id": "qdrant point id",
  "cluster_id": 12,
  "taken_at": "2026-06-01T00:00:00Z",
  "processed_at": "2026-07-10T05:00:00Z"
}
```

> **Uygulandı (Faz 2-4):** Kayıt aşamalı olarak zenginleşir. Faz 2 yalnızca keşif alanlarını yazar (`canonical_url` [unique], `shortcode`, `discovered_keywords`, `first_seen_at`/`last_seen_at`, `processing_status: "discovered"`). Faz 3 şunları ekler: ham `metrics` (like/comment/view/share/follower), `viral_score` (0-100, ayrı alan), skorun ham bileşenleri için `score_components`, `caption_text`, `transcript`, `language`, `duration_seconds`, `taken_at`, `combined_text` (caption+transcript, embedding girdisi), `processing_status` (`enriched`/`stored`). Faz 4 ise `embedding_vector_id` (Qdrant point id) ve `cluster_id` alanlarını doldurur, `processing_status`'ı `embedded`/`clustered` yapar. `owner_follower_count` kullanıcı bağlamında Graph API'den, trend bağlamında metadata sağlayıcısından gelir.

### 3.7 Embedding + Skor Birleştirme
1. `transcript + caption` metinleri birleştirilip tek bir embedding modeline verilir → **anlam vektörü** elde edilir.
2. Bu vektör, `viral_score` ile **matematiksel olarak çarpılır** (skoru normalize ettikten sonra, örn. 0-1 aralığına ölçekleyip vektörü skaler çarpma yaparak) → yeni bir **ağırlıklandırılmış vektör** elde edilir. Bu vektör hem içeriğin anlamını hem de ne kadar "iyi performans gösterdiğini" temsil eder.

> **AI Mühendisi Notu:** Ham anlam vektörünü de ayrıca (ağırlıksız haliyle) saklamanız önerilir. Çünkü skorla çarpılmış vektör benzerlik aramasında "anlam" ile "performans"ı karıştırabilir; bazı arama senaryolarında sadece anlamsal benzerlik gerekebilir. İki vektörü de Qdrant'ta ayrı payload alanlarında/collection'larda tutmak esnekliği artırır.

> **Uygulandı (Faz 4):** Öneri uygulandı — `semantic` (ham) ve `weighted` (ağırlıklı) vektörler aynı point'te ayrı named vector olarak tutulur. Ağırlıklandırma: `weighted = normalize(semantic) × scale(viral_score)`, burada `scale`, 0-100 skoru `[floor, 1]` aralığına eşler (`floor = INVOLO_WEIGHTED_VECTOR_FLOOR`, varsayılan 0.1). Böylece düşük skorlu içerik tamamen sıfırlanmaz, yönü korunurken büyüklüğü küçülür.

### 3.8 Kümeleme (Clustering)

| Yöntem | Artıları | Eksileri |
|---|---|---|
| **K-Means** | Basit, hızlı, küme sayısı önceden belirlenebilir | Küme sayısı (k) manuel seçilmeli, outlier'lara duyarlı |
| **HDBSCAN** | Küme sayısını otomatik belirler, gürültü/outlier'ları ayırt eder, yoğunluk bazlı | K-Means'e göre daha yavaş, büyük veri setlerinde bellek kullanımı yüksek olabilir |

**Öneri:** Veri seti nispeten küçükken (örn. <50k vektör) **HDBSCAN** kullanılması, küme sayısını elle ayarlamak zorunda kalmadan (içerik türleri/trendler zamanla değiştiği için) daha esnek bir çözüm sunar. Veri büyüdükçe periyodik olarak yeniden kümeleme (re-clustering) job'ı çalıştırılmalı ve `cluster_id`'ler güncellenmelidir.

> **Uygulandı (Faz 4):** Varsayılan **HDBSCAN**, alternatif **K-Means** (`INVOLO_CLUSTER_ALGORITHM` / admin panelinden `cluster_algorithm`). Ekstra native bağımlılık yerine `scikit-learn`'ün yerleşik `HDBSCAN` uygulaması kullanıldı. Kümeleme **ağırlıklı (weighted) vektörler** üzerinde çalışır; sonuç `cluster_id` hem Qdrant payload'ına hem MongoDB'ye yazılır (`recluster_trend_content` görevi). Periyodik yeniden kümeleme, cron zamanlamasında `schedule_pipeline` açıkken pipeline'ın son adımı olarak tetiklenir.

### 3.9 Qdrant'a Kayıt
- Her vektör Qdrant'a bir **point** olarak eklenir.
- Payload alanları: `mongo_id`, `cluster_id`, `viral_score`, `owner_username`, `keyword`, `taken_at`.
- Böylece hem vektör benzerliği hem de metadata filtreleme (örn. sadece belirli keyword'de arama) birlikte yapılabilir.

> **Uygulandı (Faz 4):** İki ayrı Qdrant collection'ı kullanılır: `trend_content` ve `user_averages`. Trend collection'ı **named vector**'larla kurulur — `semantic` (ham anlam vektörü) ve `weighted` (skorla ağırlıklandırılmış vektör) — böylece AI Mühendisi Notu #2'deki "ham vs. ağırlıklı vektörü ayrı tut" önerisi karşılanır ve gelecekte anlamsal veya performans-ağırlıklı arama ayrı ayrı yapılabilir. Point id, `shortcode`'dan türetilen deterministik bir UUID'dir (idempotent upsert). Payload: `mongo_id`, `shortcode`, `viral_score`, `owner_username`, `discovered_keywords`, `taken_at`, `cluster_id`. Vektör boyutu `INVOLO_VECTOR_SIZE` (varsayılan 384; fake embedding bu boyutu üretir, Bedrock/Titan boyutu bu değere göre doğrulanır).

### 3.10 Admin Paneli — Scraper Kontrolleri
- **Keyword yönetimi**: ekle/çıkar/düzenle.
- **Reels limiti**: keyword başına maksimum toplanacak içerik sayısı.
- **Headless toggle**: Bot'un tarayıcıyı görünür (debug/izleme) veya headless modda çalıştırma seçeneği.
- **Zamanlama (scheduling)**: Cron-benzeri arayüz — "her gün X saatinde çalıştır" gibi.
- **Threshold (viral eşik) ayarı**: içeriğin "viral/beğenilmiş" sayılması için minimum skor.
- **Canlı log/izleme ekranı**: bot'un o an hangi adımda olduğunu gösteren bir status feed (WebSocket ile canlı güncellenebilir).
- **Son çalıştırma özeti**: kaç içerik toplandı, kaçı threshold'u geçti, kaç tanesi kümelere eklendi.

> **Uygulandı (Faz 2-7):** Admin ekranı (`frontend`, `/admin/scraper`) şunları içerir: keyword ekle/çıkar, keyword başına limit, headless toggle, viral threshold, cron zamanlama, cluster algoritması seçimi, "scheduled pipeline" toggle; scrape başlatma ve Enrich/Embed/Recluster tetikleme butonları; pipeline istatistik paneli (discovered/enriched/stored/embedded/clustered/clusters), job polling ve cookie-authenticated WebSocket üzerinden geçmiş + canlı scraper logları. İlgili API'ler: `GET|PUT /api/v1/admin/scraper/config`, `POST /api/v1/admin/scraper/runs`, `GET .../runs/latest`, `GET .../runs/{id}`, `WS .../runs/{id}/logs`, `POST /api/v1/admin/pipeline/{enrich|embed|cluster}`, `GET /api/v1/admin/pipeline/runs/latest`, `GET /api/v1/admin/pipeline/stats`.

---

## 4. MODÜL 2 — Kullanıcı Profilleme Motoru

### 4.1 Hesap Bağlama
- Kullanıcı, **Instagram Graph API** (resmi Meta OAuth akışı, "verified by company" / Business/Creator hesap gerektirir) üzerinden kendi hesabını bağlar.
- Bağlama sonrası access token güvenli şekilde (şifrelenmiş) saklanır, refresh mekanizması kurulur.

### 4.2 Veri Çekimi
- Kullanıcının **son 30 günlük** (veya son 30 içerik, hangisi önce dolarsa) reels/içerikleri Graph API üzerinden çekilir.
- Her içerik için **Modül 1'deki aynı pipeline** uygulanır: metadata → viral skor → ses çıkarma → transcript → caption → embedding.

> **Not (kullanıcının belirttiği ayrım):** Kullanıcı verisi işlenirken **viral skor eşiği (threshold) uygulanmaz** — tüm kullanıcı içerikleri işlenir. Ayrıca, genel trend kümelerinin (Modül 1) kalitesini bozmamak için, kullanıcı verisi **ayrı bir embedding/vektör alanında** tutulabilir ve trend clustering'e otomatik dahil edilmeyebilir (bu bir tasarım tercihidir, admin panelinden açılıp kapatılabilir bir seçenek olarak sunulmalı).

### 4.3 Ortalama Vektör (User Average Vector)
- Kullanıcının son 30 içeriğinin (embedding × viral_score) vektörlerinin **ortalaması** alınarak tek bir **user average vector** oluşturulur.
- Ek olarak, bu vektörlerin **standart sapması** hesaplanabilir — bu, kullanıcının içerik çeşitliliğinin ne kadar geniş/dar olduğunu ortaya koyar ve kullanıcı profilinde bir "tutarlılık/çeşitlilik" metriği olarak sunulabilir.

### 4.4 Kullanıcı Profili (LLM ile Üretim)
- Kullanıcının; takipçi sayısı, paylaştığı içeriklerin genel teması/anlamı, ortalama performansı gibi verileri **Amazon Bedrock**'a gönderilerek bir **kullanıcı profili** (maks. ~500 token, JSON veya düz metin) üretilir.
- Bu profil, kullanıcının niş, ton, hedef kitle gibi özelliklerini özetler ve hem öneri motorunda hem de kullanıcının kendi panelinde ("Sen şöyle bir içerik üreticisisin çünkü...") gösterilir.

### 4.5 Veri Modeli — Kullanıcı Profili (MongoDB)

```json
{
  "_id": "ObjectId",
  "user_id": "internal user id",
  "instagram_username": "string",
  "connected_at": "2026-06-01T00:00:00Z",
  "last_synced_at": "2026-07-15T05:00:00Z",
  "average_vector_id": "qdrant point id",
  "vector_std_dev": 0.34,
  "ai_profile_summary": "string (<=500 token, JSON veya metin)",
  "content_count_analyzed": 27,
  "sync_frequency_cron": "0 5 * * *"
}
```

### 4.6 Zamanlama ve Tahmini Süre
- Admin, tüm kullanıcılar için profilleme job'ının **ne sıklıkla** (örn. günlük, saat 05:00) çalışacağını belirler.
- Sistem, geçmiş çalıştırmalardan **user başına ortalama işlem süresini** hesaplayarak, seçilen zaman için "Tahmini bitiş saati: 05:42" gibi bir öngörü sunar.

> **Uygulandı (Faz 5):** Resmi **Instagram API with Instagram Login** akışı production adapter olarak eklendi (`instagram_business_basic` + `instagram_business_manage_insights`); OAuth state Redis'te kullanıcıya bağlı, tek kullanımlık ve süreli tutulur. Long-lived access token authenticated encryption ile MongoDB'de saklanır ve bitişinden önce yenilenir. Credentials gerektirmeyen fixture adapter aynı akışı lokal olarak çalıştırır. Son 30 gün/en fazla 30 içerik threshold ve trend clustering uygulanmadan skorlanır, transcript edilir ve embed edilir; kullanıcı içerikleri ayrı `user_content` Mongo/Qdrant alanında tutulur. Weighted vektörlerin centroid'i `user_averages`'a, centroid etrafındaki RMS uzaklık `vector_std_dev` olarak `user_profiles`'a yazılır. Profil özeti fake veya Bedrock Converse sağlayıcısıyla (maks. 500 output token) üretilir. Celery kullanıcı/toplu profilleme görevleri, ayrı DB-tabanlı cron, kullanıcı/global lock, admin manuel tetikleme ve son 10 başarılı toplu işten kullanıcı-başı hareketli ortalama ETA hesabı eklendi. Dashboard bağlantı/profil durumunu; ayrı admin profiling ekranı cron, ETA ve job durumunu gösterir.

---

## 5. MODÜL 3 — AI İçerik Öneri Motoru

### 5.1 Tetikleme
Kullanıcı panelde **"İçerik Öner"** butonuna bastığında:
1. Kullanıcının hesabı bağlı değilse önce Instagram Graph API ile bağlanması istenir.
2. Hesap bağlıyken, Modül 2'de üretilen `average_vector` ve `ai_profile_summary` hazır olmalıdır (arka planda periyodik olarak güncellenmiş halde).

### 5.2 Benzer İçerik Getirme (Retrieval)
1. Kullanıcının `average_vector`'ü ile **Qdrant**'ta genel trend koleksiyonunda **similarity search** yapılır.
2. En yakın **K=10** (konfigüre edilebilir) vektör/nokta getirilir.
3. Bu noktalara karşılık gelen **MongoDB kayıtları** (transcript, caption, viral_score, metrikler) çekilir.

### 5.3 Prompt Oluşturma ve LLM Çağrısı
- Aşağıdaki bilgiler bir sistem promptu içinde **Amazon Bedrock**'a gönderilir:
  1. Kullanıcının `ai_profile_summary`'si (kullanıcıyı tanımlamak için)
  2. Getirilen 10 benzer/viral içeriğin transcript + caption + skor bilgileri (sektörde ne trend, onu göstermek için)
  3. Kullanıcıyı diğerlerinden ayıran, özgün, gerçekten kullanılabilir içerik fikirleri üretmesini isteyen bir sistem talimatı
- **Prompt caching** kullanılır (Bedrock'un prompt caching özelliği ile), çünkü sistem promptu ve context'in büyük kısmı (benzer içerikler) art arda gelen isteklerde tekrar kullanılabilir; bu maliyeti ve gecikmeyi azaltır.

### 5.4 Çıktı Formatı
LLM çıktısı, kullanıcı arayüzünde **kart (card) formatında** gösterilecek şekilde yapılandırılmış olmalı (JSON):

```json
{
  "recommendations": [
    {
      "title": "string",
      "hook": "string",
      "cta": "string",
      "content_format": "reels / carousel / native photo",
      "reasoning": "string (neden bu öneri bu kullanıcıya uygun)",
      "reference_cluster_id": 12
    }
  ]
}
```
- Yapay zeka **birden fazla öneri kartı** üretebilir (örn. 3-5 kart).

### 5.5 Tekrar Önleme Mekanizması
Kullanıcı, sistemin sürekli aynı içerik fikirlerini tekrar etmesini istemiyor. Önerilen çözüm:
- Kullanıcıya daha önce üretilmiş tüm önerilerin **özetleri/embedding'leri** ayrı bir MongoDB koleksiyonunda (`past_recommendations`) tutulur.
- Yeni öneri üretilmeden önce, LLM'e "daha önce şu önerileri verdin, bunları tekrar etme" şeklinde bir **negatif örnek listesi** (son N öneri) prompt'a eklenir.
- Alternatif/ek olarak: yeni üretilen önerinin embedding'i, geçmiş önerilerle **kosinüs benzerliği** üzerinden karşılaştırılır; belirli bir eşiğin üzerinde benzerlik varsa öneri reddedilip yeniden üretim istenir (retry with feedback).

### 5.6 Kullanıcı Profili Panelinde Gösterim
- Kullanıcı, kendi panelinde `ai_profile_summary` ve çeşitlilik/tutarlılık metriğine (`vector_std_dev`) dayalı, kendisi hakkında okunabilir bir analiz görebilir ("İçerik tarzın oldukça tutarlı, ağırlıklı olarak X temasına odaklanıyorsun..." gibi).

> **Uygulandı (Faz 6):** Kullanıcının `user_averages.average` vektörü ile
> `trend_content.semantic` üzerinde geniş aday havuzu aranır; semantic benzerlik ve normalize
> viral skor birlikte yeniden sıralanarak top-K bağlam seçilir. Profil özeti, sınırlandırılmış
> caption/transcript trend bağlamı ve son öneriler deterministik fake veya Bedrock Converse/Nova
> sağlayıcısına gönderilir. Bedrock yapılandırılmış tool-use çıktısı ve message `cachePoint`
> kullanır; cache kullanım token'ları kayda alınır. Üretilen kartlar embedding ile geçmiş ve aynı
> batch içindeki fikirlerle cosine/exact karşılaştırılır, tekrarlar sınırlı retry ile yenilenir ve
> tam kart sayısı oluşmadan MongoDB'ye yazılmaz. `POST|GET /api/v1/recommendations` kullanıcı
> izolasyonlu üretim/geçmiş API'sini, Redis kullanıcı lock'u paralel üretim korumasını sağlar.
> Dashboard profil hazır olana kadar aksiyonu kapatır; son kartları ve ayrı geçmiş bölümünü
> gösterir. Instagram bağlantısı kesildiğinde türetilmiş öneri geçmişi de silinir.

---

## 6. Ortak Backend Gereksinimleri

- **Framework:** FastAPI (async endpoint'ler, background task/queue entegrasyonu ile).
- **Veritabanı:** MongoDB (ham içerik, kullanıcı profili, öneri geçmişi, admin config).
- **Vektör DB:** Qdrant (trend vektörleri + kullanıcı ortalama vektörleri, ayrı collection'lar önerilir: `trend_content`, `user_averages`).
- **AI servisleri:** Amazon Bedrock (LLM + embedding), AWS Transcribe (speech-to-text).
- **Kimlik doğrulama:** Kullanıcı login/register (JWT tabanlı), admin ayrı yetkilendirme (role-based access).
- **Instagram entegrasyonu:**
  - Trend scraping → Playwright bot + kalıcı session (bizim kontrolümüzdeki hesap)
  - Kullanıcı profilleme/öneri → Instagram Graph API OAuth (resmi, kullanıcı onayı ile)

### 6.1 Önerilen MongoDB Koleksiyonları

| Koleksiyon | İçerik |
|---|---|
| `trend_content` | Modül 1'den gelen ham içerikler |
| `user_content` | Modül 2'den gelen kullanıcıya özel işlenmiş içerikler |
| `user_profiles` | Kullanıcı ortalama vektör referansı + AI profil özeti |
| `recommendations` | Üretilen öneri kartları geçmişi (tekrar önleme için) |
| `scraper_config` | Admin panelinden yönetilen keyword/limit/zamanlama ayarları |
| `job_runs` | Scraping/profiling job çalıştırma geçmişi (süre, sonuç, hata logları) |
| `users` | Uygulama kullanıcıları (login/register bilgileri) |

> **Uygulandı (Faz 0-6):** Şu koleksiyonlar oluşturulur/kullanılır: `users`, `auth_sessions` (refresh token rotasyonu), `scraper_config` (tek `key: "default"` dokümanı; keyword/limit/headless/threshold/cron/algoritma), `trend_content`, `instagram_connections` (şifreli Graph token + bağlantı durumu), `user_content`, `user_profiles`, `profiling_config`, `recommendations` (kullanıcıya ait kart batch'leri, retrieval/provider/usage ve tekrar önleme vektörleri) ve `job_runs` (`scrape`/`enrich`/`embed`/`cluster`/`profile_user`/`profile_all`).

---

## 7. UI/UX Gereksinimleri

- Genel tasarım dili: **sade, modern, güncel AI startup arayüzlerine benzer** (örn. Linear, Vercel, Notion AI tarzı — bol boşluk, net tipografi, minimal renk paleti, yumuşak gölgeler/köşeler).
- **Login / Register** ekranları standart email+şifre akışıyla.
- **Instagram hesabı bağlama** akışı (OAuth yönlendirmesi + geri dönüş durumu gösterimi).
- **Kullanıcı Paneli:**
  - Ana ekranda "İçerik Öner" butonu belirgin.
  - Öneriler kart (card) grid'i şeklinde gösterilir.
  - Kullanıcı profil özeti/analizi ayrı bir bölümde.
- **Admin Paneli:**
  - Scraper konfigürasyon ekranı (keyword, limit, headless toggle, zamanlama).
  - Canlı bot durumu / log ekranı.
  - Profiling job zamanlama + tahmini süre gösterimi.
  - Genel istatistikler (kaç içerik toplandı, kaç kullanıcı var, küme sayısı vb.).

---

## 8. Geliştirme Fazları

Aşağıdaki fazlar, projeyi bir AI geliştirme ajanına aşamalı olarak yaptırmak için tasarlanmıştır. Her faz, bir öncekinin üzerine test edilebilir şekilde inşa edilir.

### **Faz 0 — Proje İskeleti ve Altyapı** — ✅ Tamamlandı
- FastAPI proje yapısının kurulması (klasörleme: `app/api`, `app/services`, `app/models`, `app/workers`).
- MongoDB bağlantısı ve temel koleksiyon şemalarının (Pydantic modelleri) tanımlanması.
- Qdrant bağlantısı ve boş collection'ların (`trend_content`, `user_averages`) oluşturulması.
- `.env` tabanlı konfigürasyon yönetimi (AWS anahtarları, Bedrock, Instagram credentials, Qdrant URL vb.).
- Docker Compose ile lokal geliştirme ortamı (MongoDB, Qdrant, Redis, backend).
- **Uygulama notu:** Katmanlı `backend/app` (`config`, `db`, `models`, `security`, `auth`, `scraper*`, `tasks` vb.); `INVOLO_` prefixli pydantic-settings; Mongo index'leri ve Qdrant named-vector collection'ları lifespan'de idempotent kurulur; `/health/live` + gerçek bağımlılık kontrolü yapan `/health/ready`; compose `api`/`worker`/`beat`/`frontend` servisleri.

### **Faz 1 — Kullanıcı Kimlik Doğrulama ve Temel UI İskeleti** — ✅ Tamamlandı
- Register/Login endpoint'leri (JWT).
- Frontend proje kurulumu (framework tercihi geliştirme ajanına bırakılabilir; modern bir React/Next.js tabanlı yapı önerilir).
- Login, Register, boş Dashboard sayfaları.
- Admin/kullanıcı rol ayrımı.
- **Uygulama notu:** Argon2 parola hash'i; kısa ömürlü access + **rotasyonlu refresh** JWT'ler HttpOnly cookie'de, refresh token hash'i `auth_sessions`'da saklanır; `require_user`/`require_admin` bağımlılıkları; env tabanlı idempotent admin bootstrap; Next.js App Router + Tailwind, credential-aware fetch (401'de tek seferlik refresh retry).

### **Faz 2 — Playwright Scraping Botu (MVP)** — ✅ Tamamlandı
- Sabit hesap ile giriş yapan, session'ı kalıcı saklayan bot.
- Keyword bazlı arama → Reels toplama akışı (limit ile).
- Toplanan shortcode'ların MongoDB'ye ham kayıt olarak yazılması (henüz metadata/skor yok).
- Admin panelinde temel keyword/limit yönetimi ekranı.
- **Uygulama notu:** Adapter soyutlaması (`fixture` + gerçek `instagram`); kalıcı `storage_state`, kontrollü jitter/scroll, canonical URL + shortcode doğrulama; challenge/2FA/captcha'da `needs_intervention`; Redis tek-iş lock; `job_runs` durum/sayaç takibi; idempotent upsert (`discovered_keywords` korunur).

### **Faz 3 — Metadata, Skorlama ve Transcript Pipeline'ı** — ✅ Tamamlandı
- Instagram public API'den metadata çekimi.
- Viral/engagement skor hesaplama fonksiyonu.
- Video indirme → ses çıkarma → AWS Transcribe entegrasyonu.
- Transcript + caption'ın MongoDB kaydına eklenmesi.
- Threshold mekanizmasının uygulanması (admin panelinden ayarlanabilir).
- **Uygulama notu:** `metadata` (fixture/gerçek), `scoring` (0-100 normalize + recency decay, ham bileşenler ayrı), `transcription` (fake/AWS: ffmpeg→S3→Transcribe `IdentifyLanguage`), `enrichment_service` (threshold + `transcribe_min_views` maliyet ön filtresi) + `enrich_trend_content` Celery görevi. Threshold altı içerik silinmez, skorla `stored` kalır.

### **Faz 4 — Embedding ve Kümeleme** — ✅ Tamamlandı
- Transcript+caption birleştirme → embedding üretimi (Bedrock embedding modeli).
- Embedding × viral_score çarpımı ile ağırlıklı vektör üretimi.
- HDBSCAN (veya K-Means) ile kümeleme job'ı.
- Qdrant'a vektör + payload (cluster_id dahil) kaydı.
- Admin panelinde zamanlama (cron) ve headless toggle özelliklerinin eklenmesi.
- **Uygulama notu:** `embedding` (fake/Bedrock-Titan, boyut doğrulaması), `vector_service` (semantic + weighted named vector upsert, floor'lu ölçekleme, point id geri yazımı), `clustering` (sklearn HDBSCAN/K-Means) + `embed_trend_content`/`recluster_trend_content` görevleri; DB-tabanlı cron için Celery beat + `croniter`; headless config'e bağlandı.

### **Faz 5 — Kullanıcı Profilleme (Modül 2)** — ✅ Tamamlandı
- Instagram Graph API OAuth entegrasyonu (hesap bağlama).
- Son 30 günlük kullanıcı içeriği çekimi.
- Faz 3-4 pipeline'ının kullanıcı verisine uygulanması (threshold'suz).
- Ortalama vektör + standart sapma hesaplama.
- Bedrock ile kullanıcı profil özeti üretimi.
- `user_profiles` koleksiyonuna kayıt.
- Zamanlanmış toplu profilleme job'ı + tahmini süre hesaplama admin panelinde.
- **Uygulama notu:** Production Instagram Graph + lokal fixture provider, şifreli/yenilenen token, threshold'suz ortak skor/transcript/embedding pipeline'ı, ayrı user vectors, deterministic idempotent point'ler, weighted centroid + RMS çeşitlilik metriği, fake/Bedrock profil özeti, kullanıcı/toplu Celery işleri, bağımsız cron ve hareketli ortalama ETA; kullanıcı dashboard'u ve `/admin/profiling` ekranı.

### **Faz 6 — İçerik Öneri Motoru (Modül 3)** — ✅ Tamamlandı
- "İçerik Öner" butonu ve akışı.
- Qdrant üzerinde average_vector ile top-K benzerlik araması.
- Prompt oluşturma (kullanıcı profili + benzer içerikler) ve Bedrock çağrısı (prompt caching ile).
- Kart formatında öneri üretimi ve `recommendations` koleksiyonuna kayıt.
- Tekrar önleme mekanizması (geçmiş önerilerle karşılaştırma).
- **Uygulama notu:** Kullanıcı average vektörüyle semantic Qdrant araması + viral rerank,
  Mongo trend hydration, fake/Bedrock structured provider ve prompt cache; geçmiş ve batch içi
  exact/cosine dedupe + katı retry/atomik kayıt; kullanıcı Redis lock'u; üretim/geçmiş API'leri;
  profil ön koşullu öneri kartları ve ayrı geçmiş paneli.

### **Faz 7 — UI Cilalama, Kullanıcı Profil Analizi Ekranı ve Admin Dashboard**
- Modern, sade tasarım dilinin tüm ekranlara uygulanması.
- Kullanıcı profil analiz ekranı (AI özeti + çeşitlilik metriği gösterimi).
- Admin genel istatistik dashboard'u (toplam içerik, kullanıcı, küme sayısı, son job'lar).
- Canlı bot log/izleme ekranı (WebSocket ile).

### **Faz 8 — Sağlamlaştırma (Hardening) ve Test**
- Hata yönetimi (Instagram rate-limit, bot tespiti, Transcribe hataları vb. için retry/backoff mekanizmaları).
- Job kuyruğu (Celery/Redis) izleme ve hata bildirimi.
- Uçtan uca test senaryoları (scraping → skor → embedding → küme → öneri).
- Güvenlik incelemesi (credential şifreleme, token yönetimi, rate limiting).

---

## 9. Ek Öneriler (AI Mühendisi Notları — Toplu)

1. **Bot tespiti riski:** Instagram, otomatik davranışları tespit etme konusunda agresiftir. Rastgele bekleme süreleri (jitter), insan benzeri scroll/tıklama davranışları ve IP/proxy rotasyonu düşünülmeli; aksi halde hesap kısıtlanabilir/banlanabilir.
2. **Ham vs. ağırlıklı vektör ayrımı:** Bölüm 3.7'de belirtildiği gibi, anlamsal ve performans bazlı vektörleri ayrı tutmak, ileride farklı arama senaryolarına (örn. "sadece anlamca benzer, skordan bağımsız" arama) esneklik sağlar.
3. **Yeniden kümeleme (re-clustering) periyodu:** Trendler zamanla değiştiği için kümeleme tek seferlik değil, periyodik (örn. haftalık) olarak yeniden çalıştırılmalı ve eski `cluster_id`'ler güncellenmelidir.
4. **Maliyet kontrolü:** AWS Transcribe ve Bedrock çağrıları maliyetlidir; toplanan her reels için transcript çıkarmadan önce **temel bir ön filtre** (örn. minimum view count) uygulanması, gereksiz maliyeti azaltabilir.
5. **Prompt caching:** Bedrock'un prompt caching özelliği, sistem promptu ve context'in tekrarlı kısımlarını cache'leyerek hem maliyeti hem gecikmeyi düşürür; özellikle Modül 3'te sık çağrılan sabit sistem promptu için önemlidir.

---

*Bu doküman, projenin geliştirme ajanına aktarılacak birincil teknik referans kaynağıdır. Geliştirme sürecinde ortaya çıkan teknik kısıtlar (örn. Instagram API limitleri, Bedrock model kısıtları) nedeniyle bazı detaylar revize edilebilir; ancak temel akış ve veri modeli bu dokümanla uyumlu kalmalıdır.*
