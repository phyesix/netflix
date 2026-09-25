# Netflix Türkiye Top 10 → Sonarr / Radarr

Netflix'in resmi haftalık **Top 10** verisini (Tudum) takip eder, Türkiye listesindeki
**dizileri Sonarr'a**, **filmleri Radarr'a** otomatik ekler. Sürekli çalışır (varsayılan
12 saatte bir kontrol) ve daha önce eklenenleri tekrar denemez.

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

## Kurulum (Docker – önerilen)

```bash
git clone https://github.com/phyesix/netflix.git && cd netflix
cp .env.example .env
# .env içinde SONARR_API_KEY / RADARR_API_KEY ve URL'leri doldurun
# (API anahtarı: Sonarr/Radarr → Settings → General → Security → API Key)
mkdir -p data && sudo chown 1000:1000 data

# Önce deneme: hiçbir şey eklemeden ne yapacağını görün
docker compose run --rm -e DRY_RUN=true -e INTERVAL_HOURS=0 top10arr

# Sonra sürekli çalışır halde başlatın
docker compose up -d --build
docker compose logs -f
```

Sonarr/Radarr başka bir Docker ağındaysa `docker-compose.yml`'daki `networks` satırını açıp
o ağı ekleyin ya da URL'lerde sunucunun IP'sini kullanın (ör. `http://192.168.1.10:8989`).

## Kurulum (Docker'sız / cron)

Python 3.9+ yeterli, ek paket gerekmez.

```bash
set -a; . ./.env; set +a
INTERVAL_HOURS=0 python3 top10arr.py
```

crontab örneği (her gün 10:17'de):

```
17 10 * * * cd /opt/netflix && set -a && . ./.env && set +a && INTERVAL_HOURS=0 STATE_FILE=/opt/netflix/data/state.json python3 top10arr.py >> top10arr.log 2>&1
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
| `INTERVAL_HOURS` | `12` (Docker) | Kontrol aralığı; `0` tek sefer çalışır |

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
