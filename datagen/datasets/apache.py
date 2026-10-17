"""Apache access logs (combined format) of a fictional online shop.

Traffic follows a daily and weekly pattern. Some days have incidents, decided
from the seed so they are the same for everyone:
  - outage: one web server returns many 5xx errors for 10 to 45 minutes (part of its
    normal traffic, plus at least OUTAGE_ERROR_RATE errors per second, so the outage
    stays visible whatever DATAGEN_RATE is);
  - scanner: one IP probes for vulnerable URLs (lots of 404) for 5 to 20 minutes.
"""

import bisect
import math
import random
import time
from datetime import datetime, timezone
from functools import lru_cache

from datasets.company import DOMAIN, WEB_SERVERS, WEB_WEIGHTS

# name, country ISO code, country name, continent, latitude, longitude, weight
CITIES = [
    ("Paris", "FR", "France", "Europe", 48.8566, 2.3522, 20),
    ("Lyon", "FR", "France", "Europe", 45.7640, 4.8357, 8),
    ("Marseille", "FR", "France", "Europe", 43.2965, 5.3698, 6),
    ("Toulouse", "FR", "France", "Europe", 43.6047, 1.4442, 5),
    ("Bordeaux", "FR", "France", "Europe", 44.8378, -0.5792, 5),
    ("Lille", "FR", "France", "Europe", 50.6292, 3.0573, 4),
    ("Nantes", "FR", "France", "Europe", 47.2184, -1.5536, 4),
    ("Strasbourg", "FR", "France", "Europe", 48.5734, 7.7521, 3),
    ("Rennes", "FR", "France", "Europe", 48.1173, -1.6778, 3),
    ("Nice", "FR", "France", "Europe", 43.7102, 7.2620, 3),
    ("Brussels", "BE", "Belgium", "Europe", 50.8503, 4.3517, 4),
    ("Geneva", "CH", "Switzerland", "Europe", 46.2044, 6.1432, 2),
    ("London", "GB", "United Kingdom", "Europe", 51.5074, -0.1278, 4),
    ("Berlin", "DE", "Germany", "Europe", 52.5200, 13.4050, 3),
    ("Madrid", "ES", "Spain", "Europe", 40.4168, -3.7038, 2),
    ("Rome", "IT", "Italy", "Europe", 41.9028, 12.4964, 2),
    ("Amsterdam", "NL", "Netherlands", "Europe", 52.3676, 4.9041, 2),
    ("Montreal", "CA", "Canada", "North America", 45.5017, -73.5673, 3),
    ("New York", "US", "United States", "North America", 40.7128, -74.0060, 3),
    ("San Francisco", "US", "United States", "North America", 37.7749, -122.4194, 2),
    ("Casablanca", "MA", "Morocco", "Africa", 33.5731, -7.5898, 2),
    ("Dakar", "SN", "Senegal", "Africa", 14.7167, -17.4677, 1),
    ("Abidjan", "CI", "Ivory Coast", "Africa", 5.3600, -4.0083, 1),
    ("Sao Paulo", "BR", "Brazil", "South America", -23.5505, -46.6333, 1),
    ("Tokyo", "JP", "Japan", "Asia", 35.6762, 139.6503, 1),
    ("Singapore", "SG", "Singapore", "Asia", 1.3521, 103.8198, 0.5),
    ("Sydney", "AU", "Australia", "Oceania", -33.8688, 151.2093, 0.5),
]
BOT_CITY = ("Ashburn", "US", "United States", "North America", 39.0438, -77.4874, 0)

# weight, user agent, browser name, browser version, OS name, OS version, device
BROWSERS = [
    (25, "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36",
     "Chrome", "140.0.0.0", "Windows", "10", "Other"),
    (8, "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36 Edg/140.0.0.0",
     "Edge", "140.0.0.0", "Windows", "10", "Other"),
    (7, "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:143.0) Gecko/20100101 Firefox/143.0",
     "Firefox", "143.0", "Windows", "10", "Other"),
    (8, "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36",
     "Chrome", "140.0.0.0", "Mac OS X", "10.15.7", "Mac"),
    (6, "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/26.0 Safari/605.1.15",
     "Safari", "26.0", "Mac OS X", "10.15.7", "Mac"),
    (15, "Mozilla/5.0 (iPhone; CPU iPhone OS 26_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/26.0 Mobile/15E148 Safari/604.1",
     "Mobile Safari", "26.0", "iOS", "26.0", "iPhone"),
    (14, "Mozilla/5.0 (Linux; Android 15; SM-S931B) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0.0.0 Mobile Safari/537.36",
     "Chrome Mobile", "140.0.0.0", "Android", "15", "Samsung SM-S931B"),
    (2, "Mozilla/5.0 (X11; Linux x86_64; rv:143.0) Gecko/20100101 Firefox/143.0",
     "Firefox", "143.0", "Linux", "", "Other"),
    (2, "Mozilla/5.0 (Linux; Android 15; SM-A566B) AppleWebKit/537.36 (KHTML, like Gecko) SamsungBrowser/28.0 Chrome/130.0.0.0 Mobile Safari/537.36",
     "Samsung Internet", "28.0", "Android", "15", "Samsung SM-A566B"),
]
BOTS = [
    (5, "Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)",
     "Googlebot", "2.1", "Other", "", "Spider"),
    (3, "Mozilla/5.0 (compatible; bingbot/2.0; +http://www.bing.com/bingbot.htm)",
     "bingbot", "2.0", "Other", "", "Spider"),
    (1, "Mozilla/5.0 (compatible; AhrefsBot/7.0; +http://ahrefs.com/robot/)",
     "AhrefsBot", "7.0", "Other", "", "Spider"),
]
SCANNER_AGENTS = [
    ("Mozilla/5.0 zgrab/0.x", "Other", "", "Other", "", "Other"),
    ("sqlmap/1.9#stable (https://sqlmap.org)", "Other", "", "Other", "", "Other"),
    ("Mozilla/5.0 (l9scan/2.0.4; +https://leakix.net)", "Other", "", "Other", "", "Spider"),
]

CATEGORIES = ["shoes", "shirts", "jackets", "bags", "accessories", "kids", "sale"]
SEARCH_TERMS = ["sneakers", "red+dress", "leather+bag", "winter+jacket", "running", "jeans", "hat", "socks", "gift"]
EXTERNAL_REFERRERS = [
    "https://www.google.com/", "https://www.google.fr/", "https://www.bing.com/",
    "https://duckduckgo.com/", "https://www.facebook.com/", "https://www.instagram.com/",
]
MISSING_PATHS = ["/old-collection.html", "/products/99999", "/promo-summer", "/images/banner-old.png", "/blog/feed"]
SCAN_PATHS = [
    "/wp-login.php", "/wp-admin/", "/.env", "/.git/config", "/phpmyadmin/", "/admin", "/xmlrpc.php",
    "/vendor/phpunit/phpunit/src/Util/PHP/eval-stdin.php", "/cgi-bin/luci/;stok=/locale", "/config.json",
    "/backup.zip", "/server-status", "/actuator/env", "/.aws/credentials",
]
STATIC_FILES = [("/static/css/main.4f9a1c.css", 48213), ("/static/js/app.9b3e7d.js", 183447),
                ("/static/js/vendor.1c22fa.js", 412908), ("/favicon.ico", 4286)]
PRODUCTS = list(range(1000, 1600))

DATA_STREAM = "logs-apache.access-lab"
# Extra 5xx requests per second on the failing server during an outage (fixed).
OUTAGE_ERROR_RATE = 0.5


class Weighted:
    """Fast weighted random choice."""

    def __init__(self, items, weights):
        self.items = items
        self.cumulative = []
        total = 0
        for weight in weights:
            total += weight
            self.cumulative.append(total)
        self.total = total

    def pick(self, rng):
        return self.items[bisect.bisect(self.cumulative, rng.random() * self.total)]


def random_public_ip(rng):
    while True:
        a = rng.randint(1, 223)
        b = rng.randint(0, 255)
        if a in (10, 100, 127, 169, 172, 192, 198, 203):
            continue
        return f"{a}.{b}.{rng.randint(0, 255)}.{rng.randint(1, 254)}"


def geo(city):
    name, iso, country, continent, lat, lon, _ = city
    return {
        "city_name": name,
        "country_iso_code": iso,
        "country_name": country,
        "continent_name": continent,
        "location": {"lat": lat, "lon": lon},
    }


def user_agent(agent):
    original, name, version, os_name, os_version, device = agent
    ua = {"original": original, "name": name, "device": {"name": device}, "os": {"name": os_name}}
    if version:
        ua["version"] = version
    if os_version:
        ua["os"]["version"] = os_version
    return ua


class Apache:
    name = "apache"
    data_stream = DATA_STREAM
    template_name = "easy-elk-apache-access"

    def __init__(self, seed, utc_offset_hours=1, rate=1.0):
        self.seed = seed
        self.base_rate = rate
        self.utc_offset = int(utc_offset_hours * 3600)
        rng = random.Random(f"{seed}:{self.name}:reference")

        city_pick = Weighted(CITIES, [c[6] for c in CITIES])
        browser_pick = Weighted([b[1:] for b in BROWSERS], [b[0] for b in BROWSERS])
        bot_pick = Weighted([b[1:] for b in BOTS], [b[0] for b in BOTS])

        # Client pool: a few heavy users, many occasional ones, some crawlers.
        clients, weights = [], []
        for i in range(4000):
            is_bot = i < 60
            client = {
                "ip": random_public_ip(rng),
                "geo": geo(BOT_CITY if is_bot else city_pick.pick(rng)),
                "agent": user_agent(bot_pick.pick(rng) if is_bot else browser_pick.pick(rng)),
                "bot": is_bot,
                "user": None if is_bot or rng.random() > 0.25 else f"user{rng.randint(1000, 99999)}",
            }
            clients.append(client)
            weights.append(min(rng.paretovariate(1.3), 50) * (3 if is_bot else 1))
        self.clients = Weighted(clients, weights)
        self.products = Weighted(PRODUCTS, [1 / (rank + 1) ** 0.8 for rank in range(len(PRODUCTS))])
        self.hosts = Weighted(WEB_SERVERS, WEB_WEIGHTS)

        # Mean of the daily shape, so the average rate matches DATAGEN_RATE.
        self.daily_mean = sum(self.daily_shape(h / 4) for h in range(96)) / 96

        self.routes = Weighted(
            [
                ("GET", "page", lambda r: "/"),
                ("GET", "page", lambda r: f"/products?category={r.choice(CATEGORIES)}"),
                ("GET", "page", lambda r: f"/products/{self.products.pick(r)}"),
                ("GET", "page", lambda r: f"/search?q={r.choice(SEARCH_TERMS)}"),
                ("GET", "page", lambda r: "/cart"),
                ("POST", "form", lambda r: "/cart/add"),
                ("GET", "page", lambda r: "/checkout"),
                ("POST", "form", lambda r: "/checkout"),
                ("GET", "page", lambda r: "/login"),
                ("POST", "form", lambda r: "/login"),
                ("GET", "api", lambda r: f"/api/v1/products/{self.products.pick(r)}"),
                ("GET", "api", lambda r: "/api/v1/cart"),
                ("GET", "static", lambda r: r.choice(STATIC_FILES)[0]),
                ("GET", "image", lambda r: f"/images/products/{self.products.pick(r)}.jpg"),
                ("GET", "missing", lambda r: r.choice(MISSING_PATHS)),
            ],
            [10, 6, 20, 5, 4, 3, 1.5, 0.8, 2, 1.2, 6, 3, 12, 14, 1.2],
        )
        self.bot_routes = Weighted(
            [
                ("GET", "robots", lambda r: "/robots.txt"),
                ("GET", "page", lambda r: "/sitemap.xml"),
                ("GET", "page", lambda r: f"/products/{self.products.pick(r)}"),
                ("GET", "page", lambda r: f"/products?category={r.choice(CATEGORIES)}"),
                ("GET", "missing", lambda r: r.choice(MISSING_PATHS)),
            ],
            [2, 2, 12, 3, 1],
        )

    # ------------------------------------------------------------ Elasticsearch / Kibana objects

    def index_template(self):
        return {
            "index_patterns": [DATA_STREAM],
            "data_stream": {},
            "priority": 500,
            "_meta": {"description": "Apache access logs generated by easy-elk-lab datagen"},
            "template": {
                "settings": {"number_of_shards": 1, "number_of_replicas": 0},
                "mappings": {
                    "date_detection": False,
                    "dynamic_templates": [
                        {"strings_as_keyword": {
                            "match_mapping_type": "string",
                            "mapping": {"type": "keyword", "ignore_above": 1024},
                        }},
                    ],
                    "properties": {
                        "@timestamp": {"type": "date"},
                        "message": {"type": "match_only_text"},
                        "data_stream": {"properties": {
                            "type": {"type": "constant_keyword"},
                            "dataset": {"type": "constant_keyword"},
                            "namespace": {"type": "constant_keyword"},
                        }},
                        "source": {"properties": {
                            "ip": {"type": "ip"},
                            "geo": {"properties": {"location": {"type": "geo_point"}}},
                        }},
                        "http": {"properties": {
                            "response": {"properties": {
                                "status_code": {"type": "long"},
                                "body": {"properties": {"bytes": {"type": "long"}}},
                            }},
                        }},
                        "user_agent": {"properties": {
                            "original": {"type": "keyword", "fields": {"text": {"type": "match_only_text"}}},
                        }},
                    },
                },
            },
        }

    def data_view(self):
        return {
            "id": "easy-elk-apache-access",
            "title": DATA_STREAM,
            "name": "Web logs (Apache)",
            "timeFieldName": "@timestamp",
        }

    # ------------------------------------------------------------ traffic shape

    @staticmethod
    def daily_shape(hour):
        """Relative traffic by local hour: quiet at night, peaks late morning, afternoon, evening."""
        peaks = ((11, 0.8), (14.5, 1.0), (21, 0.9))
        value = 0.15
        for center, height in peaks:
            distance = abs(hour - center)
            distance = min(distance, 24 - distance)  # the day wraps around midnight
            value += height * math.exp(-(distance ** 2) / (2 * 2.2 ** 2))
        return value

    @lru_cache(maxsize=64)
    def incidents(self, day):
        """Incidents of a given UTC day (day = epoch seconds // 86400)."""
        rng = random.Random(f"{self.seed}:{self.name}:incidents:{day}")
        result = {"outage": None, "scanner": None}
        base = day * 86400
        if rng.random() < 0.4:
            start = base + rng.randint(6 * 3600, 22 * 3600)
            result["outage"] = {
                "start": start,
                "end": start + rng.randint(10, 45) * 60,
                "host": rng.choice(WEB_SERVERS),
                "error_rate": rng.uniform(0.3, 0.8),
            }
        if rng.random() < 0.5:
            start = base + rng.randint(0, 23 * 3600)
            agent = rng.choice(SCANNER_AGENTS)
            result["scanner"] = {
                "start": start,
                "end": start + rng.randint(5, 20) * 60,
                "ip": random_public_ip(rng),
                "geo": geo(rng.choice(CITIES)),
                "agent": user_agent(agent),
                "rate": 4.0,
            }
        return result

    def active(self, kind, second):
        incident = self.incidents(second // 86400)[kind]
        if incident and incident["start"] <= second < incident["end"]:
            return incident
        return None

    def base_factor(self, second):
        local = second + self.utc_offset
        hour = (local % 86400) / 3600
        weekday = (local // 86400 + 3) % 7  # 0 = Monday
        weekly = 0.75 if weekday >= 5 else 1.0
        return self.daily_shape(hour) / self.daily_mean * weekly

    def rate(self, second):
        """Events per second: normal traffic scales with DATAGEN_RATE, incidents do not."""
        rate = self.base_rate * self.base_factor(second)
        scanner = self.active("scanner", second)
        if scanner:
            rate += scanner["rate"]
        if self.active("outage", second):
            rate += OUTAGE_ERROR_RATE
        return rate

    # ------------------------------------------------------------ events

    def scheduled(self, rng, second):
        """Events at fixed times: none for this dataset."""
        return []

    def event(self, rng, ts):
        second = int(ts)
        outage = self.active("outage", second)
        if outage and rng.random() < OUTAGE_ERROR_RATE / self.rate(second):
            return self.outage_error(rng, ts, outage)
        host = self.hosts.pick(rng)

        scanner = self.active("scanner", second)
        if scanner and rng.random() < scanner["rate"] / self.rate(second):
            client = {"ip": scanner["ip"], "geo": scanner["geo"], "agent": scanner["agent"], "user": None}
            method, kind, path = "GET", "scan", rng.choice(SCAN_PATHS)
        else:
            client = self.clients.pick(rng)
            route = (self.bot_routes if client["bot"] else self.routes).pick(rng)
            method, kind, path = route[0], route[1], route[2](rng)

        status, size = self.response(rng, kind, path, method)
        if outage and outage["host"] == host and rng.random() < outage["error_rate"]:
            status = rng.choice((500, 502, 503, 503))
            size = rng.randint(380, 620)
        elif status == 200 and rng.random() < 0.002:
            status, size = 500, rng.randint(500, 560)

        referrer = self.referrer(rng, kind, client)
        return self.document(ts, host, client, method, path, status, size, referrer)

    def outage_error(self, rng, ts, outage):
        """A visitor request that fails on the server in outage."""
        client = self.clients.pick(rng)
        while client["bot"]:
            client = self.clients.pick(rng)
        method, kind, build = self.routes.pick(rng)
        status = rng.choice((500, 502, 503, 503))
        referrer = self.referrer(rng, kind, client)
        return self.document(ts, outage["host"], client, method, build(rng), status, rng.randint(380, 620), referrer)

    def response(self, rng, kind, path, method):
        if kind == "missing":
            return 404, rng.randint(190, 210)
        if kind == "scan":
            return (403, 199) if rng.random() < 0.15 else (404, rng.randint(190, 210))
        if kind in ("static", "image"):
            if rng.random() < 0.25:
                return 304, 0
            if kind == "image":
                return 200, int(rng.lognormvariate(11, 0.6))
            return 200, dict(STATIC_FILES)[path]
        if kind == "form":
            if path == "/login" and rng.random() < 0.15:
                return 200, rng.randint(7000, 9000)  # failed login: form shown again
            return 302, 0
        if kind == "api":
            return 200, rng.randint(400, 5000)
        if kind == "robots":
            return 200, 312
        return 200, int(rng.lognormvariate(10, 0.5))

    def referrer(self, rng, kind, client):
        if client.get("bot") or kind in ("scan", "robots"):
            return "-"
        if kind in ("static", "image", "api", "form"):
            return f"https://{DOMAIN}/products/{self.products.pick(rng)}" if rng.random() < 0.6 else f"https://{DOMAIN}/"
        draw = rng.random()
        if draw < 0.4:
            return "-"
        if draw < 0.75:
            return f"https://{DOMAIN}/products?category={rng.choice(CATEGORIES)}"
        return rng.choice(EXTERNAL_REFERRERS)

    def document(self, ts, host, client, method, path, status, size, referrer):
        user = client["user"]
        agent = client["agent"]
        apache_time = time.strftime("%d/%b/%Y:%H:%M:%S +0000", time.gmtime(ts))
        message = (
            f'{client["ip"]} - {user or "-"} [{apache_time}] "{method} {path} HTTP/1.1" '
            f'{status} {size or "-"} "{referrer}" "{agent["original"]}"'
        )
        url_path, _, query = path.partition("?")
        url = {"original": path, "path": url_path, "domain": DOMAIN}
        if query:
            url["query"] = query
        last = url_path.rsplit("/", 1)[-1]
        if "." in last:
            url["extension"] = last.rsplit(".", 1)[-1]

        doc = {
            "@timestamp": datetime.fromtimestamp(ts, timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z"),
            "message": message,
            "data_stream": {"type": "logs", "dataset": "apache.access", "namespace": "lab"},
            "event": {
                "dataset": "apache.access",
                "kind": "event",
                "category": ["web"],
                "type": ["access"],
                "outcome": "success" if status < 400 else "failure",
            },
            "host": {"name": host},
            "source": {"ip": client["ip"], "address": client["ip"], "geo": client["geo"]},
            "http": {
                "version": "1.1",
                "request": {"method": method},
                "response": {"status_code": status, "body": {"bytes": size}},
            },
            "url": url,
            "user_agent": agent,
        }
        if referrer != "-":
            doc["http"]["request"]["referrer"] = referrer
        if user:
            doc["user"] = {"name": user}
        return doc


class ApacheServer(Apache):
    """Normal traffic of one web server that is not in company.SERVERS (no incidents).

    Same shop, visitors and daily shape as `Apache` (same reference draws), but its own
    name in the per-second rng key: its requests never duplicate those of web-01..03.
    Used by `datagen.py file` to write a real access log on an agent server.
    """

    def __init__(self, host, **kwargs):
        super().__init__(**kwargs)
        self.name = f"apache-{host}"
        self.hosts = Weighted([host], [1])

    def active(self, kind, second):
        return None
