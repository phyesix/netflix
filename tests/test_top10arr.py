import json
import os
import sys
import tempfile
import threading
import unittest
from datetime import datetime
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlparse
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import top10arr  # noqa: E402

TSV = """country_name\tcountry_iso2\tweek\tcategory\tweekly_rank\tshow_title\tseason_title\tcumulative_weeks_in_top_10
Turkey\tTR\t2026-09-20\tFilms\t1\tNew Movie\tN/A\t1
Turkey\tTR\t2026-09-20\tFilms\t2\tOld Movie\tN/A\t3
Turkey\tTR\t2026-09-20\tTV\t1\tKızıl Goncalar\tKızıl Goncalar: Sezon 2\t2
Turkey\tTR\t2026-09-20\tTV\t2\tKızıl Goncalar\tKızıl Goncalar: Sezon 1\t2
Turkey\tTR\t2026-09-13\tTV\t1\tLast Week Show\tLast Week Show: Season 1\t1
Germany\tDE\t2026-09-20\tTV\t1\tGerman Show\tN/A\t1
"""


class ParseTests(unittest.TestCase):
    def test_latest_week_only(self):
        entries = top10arr.parse_top10(TSV, "TR", weeks=1, max_rank=10)
        self.assertEqual([(e.kind, e.title) for e in entries], [
            ("movie", "New Movie"), ("movie", "Old Movie"), ("tv", "Kızıl Goncalar")])
        self.assertEqual(entries[0].season_title, "")

    def test_multiple_weeks_and_rank(self):
        entries = top10arr.parse_top10(TSV, "TR", weeks=2, max_rank=1)
        self.assertEqual({e.title for e in entries},
                         {"New Movie", "Kızıl Goncalar", "Last Week Show"})

    def test_unknown_country(self):
        self.assertEqual(top10arr.parse_top10(TSV, "XX", 1, 10), [])

    def test_normalize(self):
        self.assertEqual(top10arr.normalize("Kızıl Goncalar"), "kizil goncalar")
        self.assertEqual(top10arr.normalize("The Crown!"), "crown")

    def test_best_match_prefers_exact_newest(self):
        results = [{"title": "Something Else", "year": 2024},
                   {"title": "Heat", "year": 1995},
                   {"title": "Heat", "year": 2025}]
        self.assertEqual(top10arr.best_match("Heat", results)["year"], 2025)
        self.assertEqual(top10arr.best_match("Nope", results)["title"], "Something Else")
        self.assertIsNone(top10arr.best_match("x", []))


class CronTests(unittest.TestCase):
    TZ = ZoneInfo("Europe/Istanbul")

    def at(self, *args):
        return datetime(*args, tzinfo=self.TZ)

    def test_daily(self):
        c = top10arr.CronSchedule("17 10 * * *")
        self.assertEqual(c.next_after(self.at(2026, 9, 25, 9, 0)), self.at(2026, 9, 25, 10, 17))
        self.assertEqual(c.next_after(self.at(2026, 9, 25, 10, 17)), self.at(2026, 9, 26, 10, 17))

    def test_steps_ranges_lists(self):
        c = top10arr.CronSchedule("0 */6 * * *")
        self.assertEqual(c.next_after(self.at(2026, 9, 25, 6, 30)), self.at(2026, 9, 25, 12, 0))
        c = top10arr.CronSchedule("30 9,21 * * 1-5")
        # 2026-09-26 is a Saturday -> next is Monday 09:30
        self.assertEqual(c.next_after(self.at(2026, 9, 25, 22, 0)), self.at(2026, 9, 28, 9, 30))

    def test_weekday_seven_is_sunday(self):
        c = top10arr.CronSchedule("0 12 * * 7")
        self.assertEqual(c.next_after(self.at(2026, 9, 25, 0, 0)), self.at(2026, 9, 27, 12, 0))

    def test_day_or_weekday(self):
        c = top10arr.CronSchedule("0 0 1 * 3")  # 1st of month OR Wednesday
        self.assertEqual(c.next_after(self.at(2026, 9, 25, 0, 0)), self.at(2026, 9, 30, 0, 0))
        self.assertEqual(c.next_after(self.at(2026, 9, 30, 0, 0)), self.at(2026, 10, 1, 0, 0))

    def test_invalid(self):
        for expr in ("* * * *", "60 * * * *", "* * * * 8", "*/0 * * * *", "a * * * *"):
            with self.assertRaises(ValueError):
                top10arr.CronSchedule(expr)


class FakeArr(BaseHTTPRequestHandler):
    posted = []
    library = {"Old Movie"}

    def log_message(self, *args):
        pass

    def _send(self, obj, code=200):
        data = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        assert self.headers["X-Api-Key"] == "key"
        url = urlparse(self.path)
        path = url.path.removeprefix("/api/v3/")
        if path == "qualityprofile":
            return self._send([{"id": 4, "name": "HD-1080p"}])
        if path == "rootfolder":
            return self._send([{"id": 1, "path": "/media/"}])
        if path == "tag":
            return self._send([])
        if path == "languageprofile":
            return self._send([], 404)
        if path in ("series/lookup", "movie/lookup"):
            term = parse_qs(url.query)["term"][0]
            item = {"title": term, "year": 2026, "tvdbId": 1, "tmdbId": 2}
            if term in self.library:
                item["id"] = 99
            return self._send([item])
        self._send({}, 404)

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        FakeArr.posted.append((self.path, body))
        if self.path == "/api/v3/tag":
            return self._send({"id": 7, "label": body["label"]}, 201)
        self._send(body, 201)


class SyncTests(unittest.TestCase):
    def setUp(self):
        FakeArr.posted = []
        self.server = HTTPServer(("127.0.0.1", 0), FakeArr)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.tmp = tempfile.TemporaryDirectory()
        base = f"http://127.0.0.1:{self.server.server_port}"
        self.tsv = os.path.join(self.tmp.name, "top10.tsv")
        with open(self.tsv, "w", encoding="utf-8") as f:
            f.write(TSV)
        self.cfg = top10arr.Config(
            country="TR", top10_url="file://" + self.tsv, weeks=1, max_rank=10,
            exclude=[], state_file=os.path.join(self.tmp.name, "state.json"),
            dry_run=False, interval_hours=0, cron_schedule=None,
            timezone="Europe/Istanbul", run_on_start=True,
            sonarr=top10arr.ArrConfig(base, "key", None, None, ["netflix-tr"], True, monitor="all"),
            radarr=top10arr.ArrConfig(base, "key", "HD-1080p", "/media", [], False,
                                      minimum_availability="released"))

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.tmp.cleanup()

    def test_sync_and_state(self):
        stats = top10arr.run_once(self.cfg)
        self.assertEqual(stats, {"added": 2, "exists": 1})
        adds = [(p, b) for p, b in FakeArr.posted if p in ("/api/v3/series", "/api/v3/movie")]
        self.assertEqual(sorted(p for p, _ in adds), ["/api/v3/movie", "/api/v3/series"])
        series = next(b for p, b in adds if p == "/api/v3/series")
        self.assertEqual(series["qualityProfileId"], 4)
        self.assertEqual(series["rootFolderPath"], "/media/")
        self.assertEqual(series["tags"], [7])
        self.assertNotIn("languageProfileId", series)
        movie = next(b for p, b in adds if p == "/api/v3/movie")
        self.assertEqual(movie["addOptions"], {"searchForMovie": False})

        FakeArr.posted = []
        stats = top10arr.run_once(self.cfg)
        self.assertEqual(stats, {"already_handled": 3})
        self.assertFalse([p for p, _ in FakeArr.posted if p != "/api/v3/tag"])

    def test_dry_run_and_exclude(self):
        self.cfg.dry_run = True
        self.cfg.exclude = [top10arr.normalize("New Movie")]
        stats = top10arr.run_once(self.cfg)
        self.assertEqual(stats, {"dry_run": 1, "excluded": 1, "exists": 1})
        self.assertFalse(os.path.exists(self.cfg.state_file))


if __name__ == "__main__":
    unittest.main()
