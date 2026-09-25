# Netflix Türkiye Top 10 → Sonarr / Radarr

Netflix'in resmi haftalık **Top 10** verisini (Tudum) takip eder, Türkiye listesindeki
**dizileri Sonarr'a**, **filmleri Radarr'a** otomatik ekler. Dahili cron ile sürekli çalışır
(varsayılan 6 saatte bir) ve daha önce eklenenleri tekrar denemez.

## Nasıl çalışır?

1. Netflix'in herkese açık veri dosyası indirilir:
   `https://www.netflix.com/tudum/top10/data/all-weeks-countries.tsv`
   (ülke bazında haftalık liste; Netflix her salı günceller).
2. `country_iso2 = TR` satırları filtrelenir, en güncel hafta(lar)ın ilk N sırası alınır.
   Aynı dizinin birden çok sezonu listede olsa bile dizi bir kez işlenir.
3. Her başlık Sonarr'ın `series/lookup` / Radarr'ın `movie/lookup` API'siyle aranır.
   Birebir başlık (orijinal / alternatif başlıklar dahil) eşleşmesi tercih edilir, birden fazla
   varsa en yeni yıl seçilir.
4. Kütüphanede yoksa belirlediğiniz kalite profili, root folder ve etiketle eklenir;
   isterseniz hemen aramayı başlatır.
5. Sonuç `state.json` içine yazılır; eklenen/var olan içerikler sonraki çalışmalarda atlanır.

## Kurulum (homelab – hazır Docker imajı)

`master`'a gelen her commit'te GitHub Actions testleri çalıştırır, imajı
`ghcr.io/phyesix/netflix` adresine (amd64, arm64, arm/v7 — Raspberry Pi dahil) yükler ve
`v1.0.N` sürümüyle bir GitHub release oluşturur. Etiketler: `latest`, `1.0.N`, `sha-<commit>`.

Zamanlama konteynerin **içinde** (dahili cron) yapılır; host'ta cron kurmanız gerekmez.

```bash
mkdir -p ~/homelab/netflix-top10 && cd ~/homelab/netflix-top10
curl -fsSLO https://raw.githubusercontent.com/phyesix/netflix/master/docker-compose.yml
curl -fsSL -o .env https://raw.githubusercontent.com/phyesix/netflix/master/.env.example
# .env içinde SONARR_API_KEY / RADARR_API_KEY ve URL'leri doldurun
# (API anahtarı: Sonarr/Radarr → Settings → General → Security → API Key)
mkdir -p data && sudo chown 1000:1000 data

# Önce deneme: hiçbir şey eklemeden ne yapacağını görün (tek sefer çalışır)
docker compose run --rm -e DRY_RUN=true -e CRON_SCHEDULE= top10arr

# Sürekli çalışır halde başlatın
docker compose up -d
docker compose logs -f
```

Tek komutla `docker run` isterseniz:

```bash
docker run -d --name netflix-top10arr --restart unless-stopped \
  --env-file .env -v "$PWD/data:/data" ghcr.io/phyesix/netflix:latest
```

Güncellemek için `docker compose pull && docker compose up -d` (veya Watchtower).
Belirli bir sürüme sabitlemek için `image: ghcr.io/phyesix/netflix:1.0.N` kullanın.

Sonarr/Radarr başka bir Docker ağındaysa `docker-compose.yml`'daki `networks` satırını açıp
o ağı ekleyin ya da URL'lerde sunucunun IP'sini kullanın (ör. `http://192.168.1.10:8989`).

> Paket ilk yayınlandığında GHCR'de **private** olabilir. Şifresiz `docker pull` için
> GitHub → profil → Packages → `netflix` → Package settings → *Change visibility → Public*.

## Zamanlama

| Değişken | Varsayılan | Açıklama |
|---|---|---|
| `CRON_SCHEDULE` | `17 */6 * * *` | 5 alanlı cron ifadesi (dakika saat gün ay haftanın-günü). `*`, `1-5`, `1,3`, `*/6` desteklenir |
| `TZ` | `Europe/Istanbul` | Cron ifadesinin yorumlandığı saat dilimi |
| `RUN_ON_START` | `true` | Konteyner açılınca hemen bir kez çalış |

`CRON_SCHEDULE` boşsa `INTERVAL_HOURS` (saat cinsinden aralık) kullanılır; o da `0` ise
program bir kez çalışıp çıkar.

## Docker'sız çalıştırma

Python 3.9+ yeterli, ek paket gerekmez. Dahili cron burada da çalışır:

```bash
set -a; . ./.env; set +a
python3 top10arr.py              # CRON_SCHEDULE'a göre sürekli çalışır
CRON_SCHEDULE= python3 top10arr.py   # tek sefer
```

## Ayarlar

Tüm ayarlar ortam değişkeniyle verilir, açıklamalar için `.env.example` dosyasına bakın.
Öne çıkanlar:

| Değişken | Varsayılan | Açıklama |
|---|---|---|
| `NETFLIX_COUNTRY` | `TR` | Başka ülke için ISO2 kodu (ör. `DE`, `US`) |
| `TOP10_MAX_RANK` | `10` | Sadece ilk N sırayı ekle |
| `TOP10_WEEKS` | `1` | Son N haftanın listesini birlikte işle |
| `EXCLUDE_TITLES` | – | İstemediğiniz başlıklar (virgülle) |
| `SONARR_MONITOR` | `all` | Yeni dizide hangi sezonlar izlensin (`latestSeason` sadece son sezon) |
| `*_SEARCH` | `true` | Eklerken indirme aramasını başlat |
| `*_TAGS` | – | Eklenen içeriklere etiket (yoksa oluşturulur) — sonradan filtrelemek/temizlemek için faydalı |
| `DRY_RUN` | `false` | Sadece logla, ekleme yapma |

Sonarr veya Radarr'dan sadece biri tanımlıysa diğer türdeki içerikler atlanır.

## Bilinen sınırlamalar

- Netflix listesi **haftalıktır** (pazartesi–pazar, salı yayınlanır); günlük liste resmi
  veri dosyasında yoktur.
- Başlıklar Netflix'in kullandığı (çoğunlukla İngilizce/uluslararası) isimle aranır. Nadiren
  yanlış eşleşme olabilir; `DRY_RUN=true` ile kontrol edebilir, sorunlu başlığı
  `EXCLUDE_TITLES`'a ekleyip Sonarr/Radarr'a elle ekleyebilirsiniz.
- Eşleşme bulunamayan başlıklar `state.json`'da `not_found` olarak kalır ve her çalışmada
  yeniden denenir (yeni çıkan içerik TVDB/TMDB'ye sonradan eklenebilir).

## Test

```bash
python3 -m unittest discover -s tests -v
```
