#!/usr/bin/env python3
"""Netflix Top 10 (country) -> Sonarr / Radarr sync.

Reads Netflix's official weekly Top 10 data (Tudum), filters it to one
country (Turkey by default), and adds the TV shows to Sonarr and the films to
Radarr. Only the Python standard library is used.
"""

from __future__ import annotations

import csv
import gzip
import http.client
import io
import json
import logging
import os
import re
import sys
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

log = logging.getLogger("top10arr")

DEFAULT_TOP10_URL = "https://www.netflix.com/tudum/top10/data/all-weeks-countries.tsv"
USER_AGENT = "top10arr/1.0 (+https://github.com/phyesix/netflix)"


# --------------------------------------------------------------------------- #
# Config
# --------------------------------------------------------------------------- #
def env(name: str, default: str | None = None) -> str | None:
    value = os.environ.get(name)
    return value.strip() if value and value.strip() else default


def env_bool(name: str, default: bool) -> bool:
    value = env(name)
    if value is None:
        return default
    return value.lower() in ("1", "true", "yes", "on", "evet")


def env_int(name: str, default: int) -> int:
    value = env(name)
    return int(value) if value is not None else default


def env_list(name: str) -> list[str]:
    value = env(name)
    return [v.strip() for v in value.split(",") if v.strip()] if value else []


@dataclass
class ArrConfig:
    url: str
    api_key: str
    quality_profile: str | None
    root_folder: str | None
    tags: list[str]
    search: bool
    monitor: str | None = None               # Sonarr only
    language_profile: str | None = None      # Sonarr v3 only
    minimum_availability: str | None = None  # Radarr only


@dataclass
class Config:
    country: str
    top10_url: str
    weeks: int
    max_rank: int
    exclude: list[str]
    state_file: str
    dry_run: bool
    interval_hours: float
    cron_schedule: str | None
    timezone: str
    run_on_start: bool
    sonarr: ArrConfig | None
    radarr: ArrConfig | None

    @classmethod
    def from_env(cls) -> "Config":
        sonarr = radarr = None
        if env("SONARR_URL") and env("SONARR_API_KEY"):
            sonarr = ArrConfig(
                url=env("SONARR_URL").rstrip("/"),
                api_key=env("SONARR_API_KEY"),
                quality_profile=env("SONARR_QUALITY_PROFILE"),
                root_folder=env("SONARR_ROOT_FOLDER"),
                tags=env_list("SONARR_TAGS"),
                search=env_bool("SONARR_SEARCH", True),
                monitor=env("SONARR_MONITOR", "all"),
                language_profile=env("SONARR_LANGUAGE_PROFILE"),
            )
        if env("RADARR_URL") and env("RADARR_API_KEY"):
            radarr = ArrConfig(
                url=env("RADARR_URL").rstrip("/"),
                api_key=env("RADARR_API_KEY"),
                quality_profile=env("RADARR_QUALITY_PROFILE"),
                root_folder=env("RADARR_ROOT_FOLDER"),
                tags=env_list("RADARR_TAGS"),
                search=env_bool("RADARR_SEARCH", True),
                minimum_availability=env("RADARR_MINIMUM_AVAILABILITY", "released"),
            )
        return cls(
            country=env("NETFLIX_COUNTRY", "TR").upper(),
            top10_url=env("TOP10_URL", DEFAULT_TOP10_URL),
            weeks=env_int("TOP10_WEEKS", 1),
            max_rank=env_int("TOP10_MAX_RANK", 10),
            exclude=[normalize(t) for t in env_list("EXCLUDE_TITLES")],
            state_file=env("STATE_FILE", "state.json"),
            dry_run=env_bool("DRY_RUN", False),
            interval_hours=float(env("INTERVAL_HOURS", "0")),
            cron_schedule=env("CRON_SCHEDULE"),
            timezone=env("TZ", "Europe/Istanbul"),
            run_on_start=env_bool("RUN_ON_START", True),
            sonarr=sonarr,
            radarr=radarr,
        )


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def normalize(title: str) -> str:
    """Lowercase, strip accents and punctuation for fuzzy title comparison."""
    title = unicodedata.normalize("NFKD", title.replace("ı", "i"))
    title = "".join(c for c in title if not unicodedata.combining(c)).lower()
    title = title.replace("&", "and")
    title = re.sub(r"[^a-z0-9]+", " ", title)
    return re.sub(r"^(the|a|an) ", "", title.strip())


def http_request(url: str, method: str = "GET", headers: dict | None = None,
                 body: dict | None = None, timeout: int = 60) -> bytes:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("User-Agent", USER_AGENT)
    if data is not None:
        req.add_header("Content-Type", "application/json")
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


# --------------------------------------------------------------------------- #
# Netflix Top 10
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class Top10Entry:
    week: str
    kind: str   # "tv" or "movie"
    rank: int
    title: str
    season_title: str


def parse_top10(tsv_text: str, country: str, weeks: int, max_rank: int) -> list[Top10Entry]:
    """Return the entries of the latest `weeks` weeks for `country`."""
    rows = [r for r in csv.DictReader(io.StringIO(tsv_text), delimiter="\t")
            if (r.get("country_iso2") or "").upper() == country]
    if not rows:
        return []
    latest_weeks = sorted({r["week"] for r in rows}, reverse=True)[:max(weeks, 1)]
    entries: dict[tuple[str, str], Top10Entry] = {}
    for r in rows:
        if r["week"] not in latest_weeks:
            continue
        rank = int(r["weekly_rank"])
        if rank > max_rank:
            continue
        kind = "tv" if r["category"].strip().lower().startswith("tv") else "movie"
        title = r["show_title"].strip()
        season = (r.get("season_title") or "").strip()
        if season.upper() == "N/A":
            season = ""
        entry = Top10Entry(r["week"], kind, rank, title, season)
        # Same show may appear in several weeks / seasons: keep one per title.
        key = (kind, normalize(title))
        if key not in entries or entry.week > entries[key].week:
            entries[key] = entry
    return sorted(entries.values(), key=lambda e: (e.kind, e.rank))


def download(url: str, attempts: int = 5, timeout: int = 120) -> bytes:
    """Download a large file robustly.

    Asks for gzip to shrink the ~30 MB TSV, and when the connection drops
    mid-transfer resumes with an HTTP Range request instead of starting over.
    """
    buf = bytearray()
    encoding = None
    expected = None
    for attempt in range(1, attempts + 1):
        req = urllib.request.Request(url)
        req.add_header("User-Agent", USER_AGENT)
        req.add_header("Accept-Encoding", "gzip")
        if buf:
            req.add_header("Range", f"bytes={len(buf)}-")
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                status = getattr(resp, "status", None) or 200
                resp_encoding = (resp.headers.get("Content-Encoding") or "").lower() or None
                if status != 206 or resp_encoding != encoding:
                    # Fresh (full) response: server ignored Range or changed encoding.
                    buf.clear()
                    encoding = resp_encoding
                    length = resp.headers.get("Content-Length")
                    expected = int(length) if length and length.isdigit() else None
                while True:
                    try:
                        chunk = resp.read(1 << 16)
                    except http.client.IncompleteRead as e:
                        buf += e.partial
                        raise
                    if not chunk:
                        break
                    buf += chunk
            if expected is not None and len(buf) < expected:
                raise http.client.IncompleteRead(b"", expected - len(buf))
            data = bytes(buf)
            return gzip.decompress(data) if encoding == "gzip" else data
        except (http.client.HTTPException, urllib.error.URLError, OSError) as e:
            if isinstance(e, urllib.error.HTTPError) and e.code == 416:
                buf.clear()  # our partial data no longer matches: restart
            if attempt == attempts:
                raise
            wait = min(2 ** attempt, 30)
            log.warning("İndirme kesildi (%s, %d bayt alındı); %d sn sonra %s (deneme %d/%d)",
                        type(e).__name__, len(buf), wait,
                        "kaldığı yerden devam edilecek" if buf else "tekrar denenecek",
                        attempt + 1, attempts)
            time.sleep(wait)
    raise RuntimeError("unreachable")


def fetch_top10(cfg: Config) -> list[Top10Entry]:
    log.info("Netflix Top 10 verisi indiriliyor: %s", cfg.top10_url)
    text = download(cfg.top10_url).decode("utf-8-sig")
    entries = parse_top10(text, cfg.country, cfg.weeks, cfg.max_rank)
    if entries:
        log.info("%s için %d içerik bulundu (hafta: %s)", cfg.country, len(entries),
                 ", ".join(sorted({e.week for e in entries})))
    else:
        log.warning("%s için Top 10 verisi bulunamadı", cfg.country)
    return entries


# --------------------------------------------------------------------------- #
# Sonarr / Radarr
# --------------------------------------------------------------------------- #
class ArrClient:
    def __init__(self, name: str, cfg: ArrConfig):
        self.name = name
        self.cfg = cfg

    def api(self, path: str, method: str = "GET", body: dict | None = None, **params):
        url = f"{self.cfg.url}/api/v3/{path}"
        if params:
            url += "?" + urllib.parse.urlencode(params)
        raw = http_request(url, method, {"X-Api-Key": self.cfg.api_key}, body)
        return json.loads(raw) if raw else None

    def resolve_quality_profile(self) -> int:
        profiles = self.api("qualityprofile")
        return self._pick(profiles, self.cfg.quality_profile, "quality profile")

    def resolve_root_folder(self) -> str:
        folders = self.api("rootfolder")
        if not folders:
            raise RuntimeError(f"{self.name}: hiç root folder tanımlı değil")
        if self.cfg.root_folder:
            for f in folders:
                if f["path"].rstrip("/") == self.cfg.root_folder.rstrip("/"):
                    return f["path"]
            raise RuntimeError(f"{self.name}: root folder bulunamadı: {self.cfg.root_folder}")
        return folders[0]["path"]

    def resolve_tags(self) -> list[int]:
        if not self.cfg.tags:
            return []
        existing = {t["label"].lower(): t["id"] for t in self.api("tag")}
        ids = []
        for label in self.cfg.tags:
            if label.lower() not in existing:
                created = self.api("tag", "POST", {"label": label})
                existing[label.lower()] = created["id"]
            ids.append(existing[label.lower()])
        return ids

    def _pick(self, items: list[dict], wanted: str | None, what: str) -> int:
        if not items:
            raise RuntimeError(f"{self.name}: hiç {what} tanımlı değil")
        if not wanted:
            return items[0]["id"]
        for item in items:
            if str(item["id"]) == wanted or item["name"].lower() == wanted.lower():
                return item["id"]
        raise RuntimeError(f"{self.name}: {what} bulunamadı: {wanted}")


def best_match(title: str, results: list[dict]) -> dict | None:
    """Pick the lookup result that best matches the Netflix title."""
    if not results:
        return None
    wanted = normalize(title)

    def names(r: dict) -> list[str]:
        alts = [a.get("title", "") for a in r.get("alternateTitles") or []]
        return [normalize(n) for n in [r.get("title", ""), r.get("originalTitle", ""), *alts] if n]

    exact = [r for r in results if wanted in names(r)]
    if exact:
        # Several exact matches (remakes etc.): Top 10 content is usually the newest.
        return max(exact, key=lambda r: r.get("year") or 0)
    return results[0]


class Sonarr(ArrClient):
    def add(self, entry: Top10Entry, dry_run: bool) -> str:
        results = self.api("series/lookup", term=entry.title)
        match = best_match(entry.title, results)
        if not match:
            return "not_found"
        label = f"{match['title']} ({match.get('year')}) [tvdb:{match.get('tvdbId')}]"
        if match.get("id"):
            log.info("  Sonarr: zaten var -> %s", label)
            return "exists"
        if dry_run:
            log.info("  Sonarr: [DRY RUN] eklenecek -> %s", label)
            return "dry_run"
        body = dict(match)
        body.update({
            "qualityProfileId": self.quality_profile_id,
            "rootFolderPath": self.root_folder,
            "monitored": True,
            "seasonFolder": True,
            "tags": self.tag_ids,
            "addOptions": {
                "monitor": self.cfg.monitor,
                "searchForMissingEpisodes": self.cfg.search,
                "searchForCutoffUnmetEpisodes": False,
            },
        })
        if self.language_profile_id is not None:
            body["languageProfileId"] = self.language_profile_id
        self.api("series", "POST", body)
        log.info("  Sonarr: eklendi -> %s", label)
        return "added"

    def prepare(self) -> None:
        self.quality_profile_id = self.resolve_quality_profile()
        self.root_folder = self.resolve_root_folder()
        self.tag_ids = self.resolve_tags()
        self.language_profile_id = None
        # Sonarr v3 requires a language profile; v4 removed the endpoint.
        try:
            profiles = self.api("languageprofile")
        except urllib.error.HTTPError:
            profiles = None
        if profiles:
            self.language_profile_id = self._pick(profiles, self.cfg.language_profile,
                                                  "language profile")


class Radarr(ArrClient):
    def add(self, entry: Top10Entry, dry_run: bool) -> str:
        results = self.api("movie/lookup", term=entry.title)
        match = best_match(entry.title, results)
        if not match:
            return "not_found"
        label = f"{match['title']} ({match.get('year')}) [tmdb:{match.get('tmdbId')}]"
        if match.get("id"):
            log.info("  Radarr: zaten var -> %s", label)
            return "exists"
        if dry_run:
            log.info("  Radarr: [DRY RUN] eklenecek -> %s", label)
            return "dry_run"
        body = dict(match)
        body.update({
            "qualityProfileId": self.quality_profile_id,
            "rootFolderPath": self.root_folder,
            "monitored": True,
            "minimumAvailability": self.cfg.minimum_availability,
            "tags": self.tag_ids,
            "addOptions": {"searchForMovie": self.cfg.search},
        })
        self.api("movie", "POST", body)
        log.info("  Radarr: eklendi -> %s", label)
        return "added"

    def prepare(self) -> None:
        self.quality_profile_id = self.resolve_quality_profile()
        self.root_folder = self.resolve_root_folder()
        self.tag_ids = self.resolve_tags()


# --------------------------------------------------------------------------- #
# State (so already handled titles are not looked up again every run)
# --------------------------------------------------------------------------- #
def load_state(path: str) -> dict:
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return {}
    except (OSError, json.JSONDecodeError) as e:
        log.warning("State dosyası okunamadı (%s), sıfırdan başlanıyor", e)
        return {}


def save_state(path: str, state: dict) -> None:
    directory = os.path.dirname(path)
    if directory:
        os.makedirs(directory, exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2, sort_keys=True)
    os.replace(tmp, path)


# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #
def run_once(cfg: Config) -> dict[str, int]:
    entries = fetch_top10(cfg)
    clients: dict[str, ArrClient] = {}
    if cfg.sonarr:
        clients["tv"] = Sonarr("Sonarr", cfg.sonarr)
    if cfg.radarr:
        clients["movie"] = Radarr("Radarr", cfg.radarr)
    for client in clients.values():
        client.prepare()

    state = load_state(cfg.state_file)
    stats: dict[str, int] = {}
    for entry in entries:
        key = f"{entry.kind}:{normalize(entry.title)}"
        tag = "Dizi" if entry.kind == "tv" else "Film"
        log.info("#%d %s: %s%s", entry.rank, tag, entry.title,
                 f" ({entry.season_title})" if entry.season_title else "")
        client = clients.get(entry.kind)
        if client is None:
            status = "skipped_no_client"
        elif normalize(entry.title) in cfg.exclude:
            status = "excluded"
        elif state.get(key, {}).get("status") in ("added", "exists"):
            status = "already_handled"
        else:
            try:
                status = client.add(entry, cfg.dry_run)
            except urllib.error.HTTPError as e:
                detail = e.read().decode(errors="replace")[:300]
                log.error("  %s hatası (%s): %s", client.name, e.code, detail)
                status = "error"
            except (urllib.error.URLError, TimeoutError, RuntimeError) as e:
                log.error("  %s hatası: %s", client.name, e)
                status = "error"
            if status == "not_found":
                log.warning("  %s: eşleşme bulunamadı", client.name)
            if status in ("added", "exists", "not_found") and not cfg.dry_run:
                state[key] = {"status": status, "title": entry.title, "week": entry.week,
                              "updated": datetime.now(timezone.utc).isoformat(timespec="seconds")}
        stats[status] = stats.get(status, 0) + 1

    if not cfg.dry_run:
        save_state(cfg.state_file, state)
    log.info("Özet: %s", ", ".join(f"{k}={v}" for k, v in sorted(stats.items())) or "boş")
    return stats


# --------------------------------------------------------------------------- #
# Built-in cron scheduler
# --------------------------------------------------------------------------- #
class CronSchedule:
    """Minimal 5-field cron expression (minute hour day month weekday).

    Supports `*`, numbers, ranges (`1-5`), lists (`1,3`) and steps (`*/6`,
    `0-30/10`). Weekday 0 and 7 are Sunday. As in classic cron, when both day
    and weekday are restricted a match on either one is enough.
    """

    RANGES = [(0, 59), (0, 23), (1, 31), (1, 12), (0, 7)]

    def __init__(self, expr: str):
        fields = expr.split()
        if len(fields) != 5:
            raise ValueError(f"Geçersiz cron ifadesi (5 alan olmalı): {expr!r}")
        self.expr = expr
        parsed = [self._parse(f, lo, hi) for f, (lo, hi) in zip(fields, self.RANGES)]
        self.minutes, self.hours, self.days, self.months, weekdays = parsed
        self.weekdays = {d % 7 for d in weekdays}
        self.day_any = fields[2] == "*"
        self.weekday_any = fields[4] == "*"

    @staticmethod
    def _parse(field: str, lo: int, hi: int) -> set[int]:
        values: set[int] = set()
        for part in field.split(","):
            rng, _, step = part.partition("/")
            if rng == "*":
                start, end = lo, hi
            elif "-" in rng:
                start, end = (int(x) for x in rng.split("-", 1))
            else:
                start = int(rng)
                end = hi if step else start
            step_n = int(step) if step else 1
            if not (lo <= start <= end <= hi) or step_n < 1:
                raise ValueError(f"Geçersiz cron alanı: {field!r}")
            values.update(range(start, end + 1, step_n))
        return values

    def _day_matches(self, dt: datetime) -> bool:
        day_ok = dt.day in self.days
        weekday_ok = (dt.isoweekday() % 7) in self.weekdays
        if self.day_any or self.weekday_any:
            return day_ok and weekday_ok
        return day_ok or weekday_ok

    def next_after(self, dt: datetime) -> datetime:
        """First matching minute strictly after `dt` (keeps dt's tzinfo)."""
        t = dt.replace(second=0, microsecond=0) + timedelta(minutes=1)
        limit = t + timedelta(days=366 * 5)
        while t < limit:
            if t.month not in self.months or not self._day_matches(t):
                t = (t + timedelta(days=1)).replace(hour=0, minute=0)
            elif t.hour not in self.hours:
                t = (t + timedelta(hours=1)).replace(minute=0)
            elif t.minute not in self.minutes:
                t += timedelta(minutes=1)
            else:
                return t
        raise ValueError(f"Cron ifadesi hiç eşleşmiyor: {self.expr!r}")


def sleep_until(target: datetime) -> None:
    while True:
        remaining = (target - datetime.now(target.tzinfo)).total_seconds()
        if remaining <= 0:
            return
        time.sleep(min(remaining, 300))


def safe_run(cfg: Config) -> bool:
    try:
        run_once(cfg)
        return True
    except Exception:  # keep the daemon alive, retry on the next schedule
        log.exception("Çalıştırma başarısız")
        return False


def main() -> int:
    logging.basicConfig(level=env("LOG_LEVEL", "INFO").upper(),
                        format="%(asctime)s %(levelname)s %(message)s")
    cfg = Config.from_env()
    log.info("top10arr %s başlıyor (ülke: %s)", env("APP_VERSION", "dev"), cfg.country)
    if not cfg.sonarr and not cfg.radarr:
        log.warning("SONARR_URL/SONARR_API_KEY veya RADARR_URL/RADARR_API_KEY tanımlı değil; "
                    "sadece liste gösterilecek")

    if cfg.cron_schedule:
        tz = ZoneInfo(cfg.timezone)
        schedule = CronSchedule(cfg.cron_schedule)
        log.info("Cron modu: '%s' (%s)", schedule.expr, cfg.timezone)
        if cfg.run_on_start:
            safe_run(cfg)
        while True:
            next_run = schedule.next_after(datetime.now(tz))
            log.info("Sonraki çalışma: %s", next_run.strftime("%Y-%m-%d %H:%M %Z"))
            sleep_until(next_run)
            safe_run(cfg)

    if cfg.interval_hours > 0:
        while True:
            safe_run(cfg)
            log.info("%.1f saat sonra tekrar çalışacak", cfg.interval_hours)
            time.sleep(cfg.interval_hours * 3600)

    return 0 if safe_run(cfg) else 1


if __name__ == "__main__":
    sys.exit(main())
