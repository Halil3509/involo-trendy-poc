# Involo POC — Manuel / E2E Test Planı

> **Amaç:** `run-local.sh` ile yerel ortamı ayağa kaldırarak, MCP Playwright kullanarak tüm kullanıcı/admin akışlarına tıklayarak denemek, oluşan durumu, hataları ve iyileştirme önerilerini raporlamak.
> **Kapsam:** Frontend (`http://localhost:8020`) + Backend (`http://localhost:8021`) + altyapı (MongoDB, Redis, Qdrant, MinIO). Gerçek Instagram / AWS / Bedrock entegrasyonları yalnızca `.env`'de açıkça etkinleştirilirse dener; varsayılan sahte (`fake`) provider'larla çalışılır.

---

## 1. Hazırlık ve Ön Koşullar

### 1.1 Ortam kontrolü
- [ ] `.env` dosyası mevcut ve `INVOLO_JWT_SECRET` dolu.
- [ ] `backend/.venv` kurulu, `uv` çalışıyor.
- [ ] `frontend/node_modules` kurulu ve güncel.
- [ ] Docker çalışıyor ve `docker compose` mevcut.

### 1.2 Altyapıyı başlat
```bash
make infra-up
# MinIO bucket'larını oluştur
docker compose -f docker-compose.infra.yml -p involo --profile init run --rm minio-init
```

### 1.3 Uygulamayı başlat
```bash
./run-local.sh up
```
- API: `http://localhost:8021`
- UI: `http://localhost:8020`
- OpenAPI: `http://localhost:8021/docs`
- MinIO Console: `http://localhost:8027`

### 1.4 İzleme araçları
- [ ] API logları: `tail -f logs/uvicorn.log` veya `make logs`
- [ ] Worker logları: `tail -f logs/celery-*.log`
- [ ] Browser console ve network sekmesi (Playwright `mcp3_browser_console_messages` / `mcp3_browser_network_requests`)

---

## 2. Smoke / Sağlık Testleri (Öncelik: Kritik)

| # | Adım | Beklenen | Notlar |
|---|------|----------|--------|
| 2.1 | `GET http://localhost:8021/health/live` | `{"status":"ok"}` | Temel API canlılık. |
| 2.2 | `GET http://localhost:8021/health/ready` | `{"status":"ok"}` veya 503 | Mongo/Redis/Qdrant/S3/Bedrock probe sonuçlarını yansıtır. `INVOLO_PROVIDER_READINESS_PROBES_ENABLED=true` ise gerçek provider'lara dokunur; sahte provider modunda genellikle `ok`. |
| 2.3 | `GET http://localhost:8021/docs` | Swagger/OpenAPI sayfası yükleniyor | Route listesi, schema doğruluğu kontrol. |
| 2.4 | `GET http://localhost:8020` | `/login` veya `/dashboard` yönlendirmesi | Next.js 16 derleniyor, 404 yok. |
| 2.5 | MongoDB, Redis, Qdrant, MinIO portlarına telnet/curl | Bağlantı açık | 8022, 8024, 8026, 8028. |

---

## 3. Kimlik Doğrulama ve Onboarding (Öncelik: Kritik)

### 3.1 Kayıt (Register)
- [ ] `/register` git.
- [ ] Geçersiz e-posta → hata mesajı.
- [ ] 10 karakterden kısa şifre → hata mesajı.
- [ ] Eşleşmeyen şifre → hata mesajı.
- [ ] Geçerli kayıt → `/onboarding` yönlendirmesi ve kullanıcı oluşur (Mongo `users`).

### 3.2 Giriş (Login)
- [ ] `/login` git.
- [ ] Yanlış şifre → 401 ve anlamlı hata.
- [ ] Doğru bilgiler → `/dashboard` yönlendirmesi.
- [ ] Cookie `access_token` ve `refresh_token` HttpOnly olarak geliyor mu? (network response headers)
- [ ] `/api/v1/auth/me` dönüşü doğru `email` ve `role` içeriyor mu?

### 3.3 Onboarding
- [ ] `/onboarding` formunu doldur: hedef pazar, dil, zaman dilimi, niş, hedefler, kısıtlar.
- [ ] Zorunlu alanlar boşsa validation hatası.
- [ ] Kaydet → `/dashboard` yönlendirmesi ve `CreatorPreferences` güncelleniyor.

### 3.4 Oturum yönetimi
- [ ] Sayfa yenileme sonrası oturum korunuyor mu?
- [ ] Çıkış (Sign out) → `/login` yönlendirmesi, cookie'ler temizleniyor.
- [ ] Refresh endpoint (`/api/v1/auth/refresh`) 401 durumunda tekrar girişe atıyor mu?

---

## 4. Dashboard ve İçerik Önerileri (Öncelik: Yüksek)

### 4.1 Dashboard yüklenimi
- [ ] Kullanıcı e-posta ve rolü görünür.
- [ ] Admin kullanıcıda "Open scraper control" butonu görünür; normal kullanıcıda görünmez.
- [ ] `InstagramProfileCard` durumuna göre mesaj: `disconnected`, `connected`, `profiling`, `ready`, `failed`, `needs_reauth`.

### 4.2 Instagram bağlantı durumları
- [ ] Bağlı değilken "Connect Instagram" butonu görünür.
- [ ] Buton tıklanınca popup açılır (`window.open` ile `authorization_url`).
- [ ] OAuth callback sonrası durum `connected`/`ready` olur ve mesaj gösterilir.
- [ ] "Analyze now" tıklanınca profile sync job başlar, `profiling` durumunda spinner güncellenir.
- [ ] "Disconnect" tıklayınca onay penceresi, ardından `disconnected` durumu.

### 4.3 Recommendations
- [ ] Instagram `ready` olmadan "Generate 3 ideas" butonu disabled.
- [ ] Profil `ready` olduktan sonra öneri oluştur; en fazla 3 kart gelmeli.
- [ ] Her kartta: format, başlık, hook, CTA, `Why it fits`, evidence aç/kapa, shoot plan aç/kapa.
- [ ] `Save`, `Dismiss`, `Start production`, `Link published post`, `Create hook experiment` aksiyonları tıklanarak denenir.
- [ ] State değişiklikleri Mongo `recommendation_events`'e yazılıyor mu? (API üzerinden veya admin job loglarından kontrol)

---

## 5. Profil ve Analytics (Öncelik: Yüksek)

### 5.1 `/profile`
- [ ] Instagram profil kartı aynı şekilde çalışıyor.
- [ ] `ProfileAnalytics` yükleniyor (pillars, patterns, audience markets, data quality).
- [ ] Öneri geçmişi listeleniyor; boşsa uygun mesaj.

### 5.2 API ile çapraz kontrol
- [ ] `GET /api/v1/profile/analytics` yanıt şeması `ProfileAnalytics` ile uyumlu.
- [ ] `GET /api/v1/recommendations` limit/offset davranışı doğru.

---

## 6. Creator Tracking (Öncelik: Yüksek)

### 6.1 `/creators`
- [ ] Listeyi çek (başlangıçta boş olabilir).
- [ ] Geçersiz username ekleme → hata.
- [ ] Geçerli public creator ekle (örn. `natgeo` veya sahte fixture modunda known handle).
- [ ] Ekleme sonrası satır: username, display name, followers, trend score, status.
- [ ] `Remove` butonu ile listeden kaldır.

### 6.2 `/creators/{id}`
- [ ] Detay sayfası yükleniyor.
- [ ] Follower history `week/month/year` butonları değiştikçe grafik güncelleniyor.
- [ ] `Analyze now` ile creator AI profile job başlatılıyor.
- [ ] Top content grid geliyor; caption, views, likes, comments, viral score görünür.
- [ ] Permalink varsa "View on Instagram" açılıyor.

### 6.3 API ile çapraz kontrol
- [ ] `POST /api/v1/creators`, `GET /api/v1/creators/{id}`, `GET /api/v1/creators/{id}/followers?range=month`, `GET /api/v1/creators/{id}/content`, `POST /api/v1/creators/{id}/analyze`, `DELETE /api/v1/creators/{id}`.

---

## 7. Admin Paneli (Öncelik: Yüksek)

> Admin erişimi için `INVOLO_BOOTSTRAP_ADMIN_EMAIL` / `PASSWORD` ile giriş veya veritabanında `role=admin` yapılması gerekir.

### 7.1 `/admin`
- [ ] Overview metrics: users, connected Instagram, trend content, profiles ready, recommendations.
- [ ] Attention panel: failed/needs intervention job'lar, needs reauth sayısı.
- [ ] Recent jobs tablosu: kind, state, counters, created.
- [ ] Observability bölümü: latency, cost, funnel, telemetry tablosu.

### 7.2 `/admin/scraper`
- [ ] Config yükleniyor: keywords, reels per keyword, viral threshold, cron, headless, schedule pipeline.
- [ ] Keyword ekle/çıkar, Enter veya virgül ile ekleme.
- [ ] Geçersiz cron girildiğinde `Save configuration` disabled/hata.
- [ ] Config kaydetme: `PUT /api/v1/admin/scraper/config` 200.
- [ ] `Start scrape`: `POST /api/v1/admin/scraper/runs` job dönüyor.
- [ ] Pipeline: `Run full pipeline`, `Enrich`, `Embed` butonları.
- [ ] Active job sırasında `Stop` butonu çalışıyor.
- [ ] Live pipeline log akıyor (WebSocket / log polling).

### 7.3 Trend content tablosu
- [ ] Filtreler: status, action, keyword, search, sort.
- [ ] Sayfalama önceki/sonraki.
- [ ] Bir satıra tıklayınca detail dialog açılıyor.
- [ ] Detail dialog'da: caption, metrics, score components, transcript, visual analysis, embedding vector id, processing regions vb. görünür.

### 7.4 `/admin/profiling`
- [ ] Capacity estimate yükleniyor.
- [ ] Automatic profiling toggle + cron kaydetme.
- [ ] Geçersiz cron validation.
- [ ] `Run profiling now` job başlatıyor.
- [ ] Latest run state, counters, error gösteriliyor.

### 7.5 `/admin/brand-analysis`
- [ ] Instagram URL/username ve max posts (1-30) ile analiz başlatma.
- [ ] Validation: boş, geçersiz URL, max posts sınır dışı.
- [ ] Analiz başlatınca job state polling ile güncelleniyor (`queued` → `running` → `analyzed`/`succeeded`/`failed`).
- [ ] Toplanan gönderiler listeleniyor.
- [ ] Rapor yüklendiğinde `BrandAnalysisReport` kartı görünür.
- [ ] `Copy report` panoya kopyalıyor.
- [ ] `Export PDF` indirme başlatıyor (sahte provider'da placeholder PDF olabilir).
- [ ] Live log akıyor.

### 7.6 API ile çapraz kontrol
- [ ] `GET /api/v1/admin/overview`
- [ ] `GET /api/v1/admin/observability`
- [ ] `GET /api/v1/admin/jobs?state=&kind=`
- [ ] `POST /api/v1/admin/scraper/runs`, `PUT /api/v1/admin/scraper/config`
- [ ] `POST /api/v1/admin/pipeline/run`, `POST /api/v1/admin/pipeline/enrich`, `POST /api/v1/admin/pipeline/embed`
- [ ] `POST /api/v1/admin/profiling/runs`, `PUT /api/v1/admin/profiling/config`
- [ ] `POST /api/v1/admin/brand-analysis/runs`, `GET /api/v1/admin/brand-analysis/runs/{id}`, `GET /api/v1/admin/brand-analysis/reports/{id}`, `GET /api/v1/admin/brand-analysis/reports/{id}/pdf`

---

## 8. Hata, Boş ve Edge Durumları (Öncelik: Orta)

- [ ] Backend kapalıyken frontend'de network hatası anlamlı mesaj veriyor.
- [ ] 401 durumunda kullanıcı `/login`'e atılıyor.
- [ ] 403 admin-only route'lara normal kullanıcı erişemiyor.
- [ ] Veritabanı boşken tüm sayfalar "No ... yet" mesajı gösteriyor.
- [ ] Sayfalar arası hızlı geçişlerde loading/error state tutarlı.
- [ ] Mobil genişlikte (Playwright `mcp3_browser_resize`) navigasyon ve tablolar kırılmıyor.
- [ ] A11y: form label'ları, button aria-label'ları, heading hiyerarşisi (Playwright `getByRole` ile kontrol).

---

## 9. Playwright / MCP Otomasyon Adımları

> MCP Playwright (`mcp3_*`) araçları kullanılarak manuel testler otomatize edilecek. Her adımda screenshot + console/network log alınacak.

### 9.1 Başlangıç
```text
1. mcp3_browser_navigate ile http://localhost:8020/login aç.
2. mcp3_browser_fill_form: email, password, submit.
3. mcp3_browser_wait_for text "Good to see you" veya URL /dashboard.
4. mcp3_browser_console_messages level=error al.
5. mcp3_browser_network_requests ile /api/v1/auth/login isteğini incele.
```

### 9.2 Keşif turu
```text
- Her navigasyon linkine tıkla: Dashboard, Creators, My profile, Admin, Scraper, Profiling, Brand analysis.
- Her sayfada: heading, loading, hata, boş durum kontrolü.
- Formları doldur: onboarding, add creator, scraper config, brand analysis input.
- Butonlara tıkla: start scrape, run pipeline, run profiling, analyze now, generate ideas, save/dismiss recommendation, export PDF.
```

### 9.3 Veri doğrulama
```text
- API endpoint'lerine curl ile eş zamanlı çağrılar at:
  GET /api/v1/auth/me
  GET /api/v1/preferences
  GET /api/v1/instagram/status
  GET /api/v1/admin/overview
  GET /api/v1/admin/jobs
- Frontend gösterimi ile API yanıtlarını karşılaştır.
```

### 9.4 Log ve ekran görüntüsü
```text
- Her test adımında mcp3_browser_take_screenshot ile isimli screenshot.
- Hata durumunda console messages + network requests kaydet.
- Backend loglarından ilgili job/error traceback'leri çıkar.
```

---

## 10. Test Sırası ve Önceliklendirme

| Sıra | Bölüm | Öncelik | Tahmini Süre |
|------|-------|---------|--------------|
| 1 | Hazırlık & sağlık | Kritik | 5 dk |
| 2 | Auth / Register / Login / Onboarding | Kritik | 10 dk |
| 3 | Dashboard & Recommendations | Yüksek | 15 dk |
| 4 | Profile & Analytics | Yüksek | 10 dk |
| 5 | Creators Tracking | Yüksek | 15 dk |
| 6 | Admin Overview / Observability | Yüksek | 10 dk |
| 7 | Admin Scraper & Pipeline | Yüksek | 20 dk |
| 8 | Admin Profiling | Yüksek | 10 dk |
| 9 | Admin Brand Analysis | Yüksek | 15 dk |
| 10 | Hata / Boş / Edge / Responsive | Orta | 15 dk |
| 11 | API çapraz kontrol & log inceleme | Yüksek | 15 dk |
| 12 | Rapor hazırlama | Kritik | 15 dk |

**Toplam tahmini süre:** ~2.5–3 saat (sahte provider modunda). Gerçek Instagram/AWS entegrasyonları eklenirse süre artar.

---

## 11. Beklenen Çıktılar

Her test sonrası aşağıdaki tablolar güncellenecek:

### 11.1 Durum Özeti
| Bölüm | Durum | Not |
|-------|-------|-----|
| Auth | ⬜ | |
| Dashboard | ⬜ | |
| Recommendations | ⬜ | |
| Creators | ⬜ | |
| Admin Scraper | ⬜ | |
| Admin Profiling | ⬜ | |
| Brand Analysis | ⬜ | |

### 11.2 Bulgu / Bug Şablonu
| ID | Sayfa / Endpoint | Sorun | Önem | Ekran / Log | Öneri |
|----|-------------------|-------|------|-------------|-------|
| BUG-1 | | | | | |

### 11.3 İyileştirme Önerileri
| ID | Alan | Öneri | Beklenen Etki |
|----|------|-------|---------------|
| IMP-1 | | | |

---

## 12. Sınırlar ve Dikkat Edilecekler

- **Gerçek provider'lar:** `.env` içinde `INVOLO_EMBEDDING_PROVIDER=aws`, `INVOLO_VISION_PROVIDER=aws`, `INVOLO_TRANSCRIPTION_PROVIDER=aws` gibi ayarlar yapılmadan AWS/Bedrock çağrıları yapılmayacak.
- **Instagram OAuth:** Popup tabanlıdır; Playwright ile `mcp3_browser_tabs` ve popup yönetimi gerekir. Gerçek Instagram hesabı ve Meta app ayarları gerekebilir; yoksa sadece popup açılışı ve callback'e geri dönüş test edilir.
- **Creator tracking:** Default `graph_api` modu için Meta token gerekir. `INVOLO_CREATOR_TRACKING_PROVIDER=fixture` ile local JSON fixture kullanılabilir.
- **Brand analysis PDF:** `playwright` provider'ı Chromium ister; sahte modda `fake` provider placeholder byte dönebilir.
- **Rate limit:** Hızlı ardışık buton tıklamaları `INVOLO_AUTH_RATE_LIMIT` / `INVOLO_RECOMMENDATION_RATE_LIMIT` sınırlarına ulaştırabilir.
- **Veri temizliği:** Test sonrası MongoDB/Qdrant/S3 üzerinde oluşan test verileri silinmelidir (`DELETE /api/v1/instagram/connection`, admin job logları, gereksiz Qdrant point'leri).

---

## 13. Sonraki Adımlar (Rapor Sonrası)

1. Bulunan hatalar için GitHub issue'ları aç.
2. Düşük riskli, yüksek etkili iyileştirmeleri PR olarak uygula.
3. E2E testlerini `frontend/e2e/` altına Playwright spec olarak otomatize et.
4. `make verify` ile backend/frontend gate'lerinin hâlâ geçtiğini doğrula.
