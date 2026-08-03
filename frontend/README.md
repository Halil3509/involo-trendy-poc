# Involo frontend

Next.js 16 App Router, React 19, TypeScript ve Tailwind CSS 4 ile geliştirilen
Involo kullanıcı/admin arayüzüdür.

## Ekranlar

- `/login`, `/register`: cookie tabanlı kimlik doğrulama
- `/dashboard`: Instagram bağlantısı, profil durumu ve içerik önerileri
- `/profile`: AI profil analizi
- `/admin`: sistem özeti ve job gözlemi
- `/admin/scraper`: scraper/pipeline yönetimi ve canlı log
- `/admin/profiling`: toplu profilleme zamanlaması ve ETA

## Yerel çalışma

```bash
npm install
printf 'NEXT_PUBLIC_API_URL=http://localhost:8021\n' > .env.local
npm run dev
```

Uygulama `http://localhost:8020` adresinde açılır. Backend çalışıyor olmalıdır.
`NEXT_PUBLIC_*` değişkenleri browser'a açıktır; secret içermemelidir.

## Komutlar

```bash
npm run lint
npm run typecheck
npm test -- --run
npm run build
```

API çağrıları `src/lib/api.ts`, paylaşılan kontratlar `src/lib/types.ts` içindedir.
İstekler HttpOnly auth cookies için `credentials: "include"` kullanır. 401
cevabında client yalnızca bir refresh ve retry dener.

Tam sistem mimarisi ve geliştirme rehberi:
[`../docs/PROJECT_ARCHITECTURE.md`](../docs/PROJECT_ARCHITECTURE.md).
