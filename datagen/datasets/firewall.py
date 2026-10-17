"""Firewall traffic logs (FortiGate key=value format) of the company running the shop.

Network behind the firewall "fw-01":
  wan1     203.0.113.10       public IP (source NAT for outbound traffic)
  dmz      172.16.10.11-13    web-01, web-02, web-03: the shop (see the apache dataset)
  lan      10.10.20.0/24      employee workstations
  servers  10.10.10.0/24      internal servers (DNS, Active Directory, files, intranet,
                              db-01: the shop database)
(addresses come from datasets/company.py)

Traffic: employees using the Internet and internal servers (office hours), customers
reaching the shop (same visitors and pattern as the apache dataset), the web servers
querying the database (same pattern; during an apache outage the failing server keeps
opening very short connections, like the "too many clients" of the postgresql dataset),
DNS, and the usual Internet background noise blocked by the firewall.

Incidents, on some days only (decided from the seed):
  - portscan: one Internet IP probes hundreds of ports (denied), 2 to 10 minutes;
  - beaconing: an infected workstation contacts a command-and-control server about
    every minute, from some hour until the end of the day;
  - exfiltration: at night, a workstation uploads large amounts of data to an
    unknown server for 30 to 90 minutes;
  - the web scanner of the apache dataset also shows up here (same IP, same time).
"""

import random
import time
from datetime import datetime, timezone
from functools import lru_cache

from datasets.apache import Apache, Weighted, random_public_ip
from datasets.company import DB_PORT, WAN_IP, WEB_SERVERS as WEB_NAMES, WEB_WEIGHTS, ip

DATA_STREAM = "logs-firewall.traffic-lab"

FW_NAME = "fw-01"
FW_SERIAL = "FG100FTK23000417"
WEB_SERVERS = {name: ip(name) for name in WEB_NAMES}
DNS_SERVER = ip("dns-01")
DB_SERVER = ip("db-01")

# port: (FortiGate service name, transport, application protocol)
SERVICES = {
    22: ("SSH", "tcp", "ssh"), 23: ("TELNET", "tcp", "telnet"), 25: ("SMTP", "tcp", "smtp"),
    53: ("DNS", "udp", "dns"), 80: ("HTTP", "tcp", "http"), 88: ("KERBEROS", "tcp", "kerberos"),
    123: ("NTP", "udp", "ntp"), 161: ("SNMP", "udp", "snmp"), 389: ("LDAP", "tcp", "ldap"),
    443: ("HTTPS", "tcp", "https"), 445: ("SMB", "tcp", "smb"), 1433: ("MS-SQL", "tcp", "tds"),
    2323: ("tcp/2323", "tcp", None), 3306: ("MYSQL", "tcp", "mysql"), 3389: ("RDP", "tcp", "rdp"),
    5060: ("SIP", "udp", "sip"), 5900: ("VNC", "tcp", "vnc"), 6379: ("tcp/6379", "tcp", "redis"),
    8080: ("tcp/8080", "tcp", "http"), 8443: ("tcp/8443", "tcp", None), 9200: ("tcp/9200", "tcp", None),
    37215: ("tcp/37215", "tcp", None), DB_PORT: ("PGSQL", "tcp", "postgresql"),
}
PROTO_NUMBERS = {"tcp": 6, "udp": 17}

# id, name
POLICY_LAN_INTERNET = (1, "LAN-to-Internet")
POLICY_DNS_INTERNET = (2, "DNS-to-Internet")
POLICY_LAN_SERVERS = (3, "LAN-to-Servers")
POLICY_INTERNET_WEB = (4, "Internet-to-DMZ-Web")
POLICY_BLOCK_MALICIOUS = (5, "Block-Malicious-Destinations")
POLICY_DMZ_DATABASE = (6, "DMZ-Web-to-Database")
POLICY_IMPLICIT_DENY = (0, "Implicit-Deny")

# Outbound destinations: organization, country ISO, country, continent, lat, lon, prefixes, weight
ONLINE_SERVICES = [
    ("Microsoft Corporation", "IE", "Ireland", "Europe", 53.3498, -6.2603, ["52.97", "40.126", "20.190"], 22),
    ("Google LLC", "US", "United States", "North America", 37.4220, -122.0841, ["142.250", "172.217", "216.58"], 20),
    ("Amazon.com, Inc.", "FR", "France", "Europe", 48.8566, 2.3522, ["13.36", "15.188", "35.180"], 10),
    ("Cloudflare, Inc.", "US", "United States", "North America", 37.7749, -122.4194, ["104.16", "172.67"], 10),
    ("Akamai International B.V.", "NL", "Netherlands", "Europe", 52.3676, 4.9041, ["23.32", "2.16"], 8),
    ("Fastly, Inc.", "US", "United States", "North America", 37.7749, -122.4194, ["151.101"], 6),
    ("OVH SAS", "FR", "France", "Europe", 50.6942, 3.1746, ["51.68", "54.36", "141.94"], 6),
    ("Zoom Video Communications, Inc.", "US", "United States", "North America", 37.3382, -121.8863, ["170.114", "3.7"], 5),
    ("Meta Platforms, Inc.", "IE", "Ireland", "Europe", 53.3498, -6.2603, ["157.240", "31.13"], 4),
    ("Orange S.A.", "FR", "France", "Europe", 48.8566, 2.3522, ["193.252", "80.12"], 3),
    ("Salesforce, Inc.", "US", "United States", "North America", 37.7897, -122.3972, ["136.146"], 3),
]
DNS_RESOLVERS = [
    ("1.1.1.1", "Cloudflare, Inc.", "US", "United States", "North America", 37.7749, -122.4194),
    ("8.8.8.8", "Google LLC", "US", "United States", "North America", 37.4220, -122.0841),
    ("9.9.9.9", "Quad9", "CH", "Switzerland", "Europe", 47.3769, 8.5417),
]
# Countries of Internet scanners and suspicious servers: ISO, name, continent, lat, lon, weight
COUNTRIES = [
    ("CN", "China", "Asia", 39.9042, 116.4074, 20), ("US", "United States", "North America", 38.9072, -77.0369, 12),
    ("RU", "Russia", "Europe", 55.7558, 37.6173, 10), ("NL", "Netherlands", "Europe", 52.3676, 4.9041, 6),
    ("IN", "India", "Asia", 19.0760, 72.8777, 6), ("DE", "Germany", "Europe", 50.1109, 8.6821, 5),
    ("BR", "Brazil", "South America", -23.5505, -46.6333, 5), ("VN", "Vietnam", "Asia", 21.0278, 105.8342, 5),
    ("KR", "South Korea", "Asia", 37.5665, 126.9780, 4), ("FR", "France", "Europe", 48.8566, 2.3522, 3),
    ("GB", "United Kingdom", "Europe", 51.5074, -0.1278, 3), ("IR", "Iran", "Asia", 35.6892, 51.3890, 3),
    ("TW", "Taiwan", "Asia", 25.0330, 121.5654, 3), ("HK", "Hong Kong", "Asia", 22.3193, 114.1694, 3),
    ("BG", "Bulgaria", "Europe", 42.6977, 23.3219, 2), ("SG", "Singapore", "Asia", 1.3521, 103.8198, 2),
]
NOISE_PORTS = [
    (22, 15), (23, 12), (3389, 10), (445, 8), (80, 6), (8080, 6), (1433, 4), (3306, 4), (5900, 4),
    (25, 3), (443, 3), (2323, 3), (6379, 3), (9200, 3), (5060, 3), (123, 2), (161, 2), (37215, 2),
]
# Internal services used by employees: server IP, port, weight
INTERNAL_SERVICES = [
    (ip("dns-01"), 53, 8), (ip("dc-01"), 389, 4), (ip("dc-01"), 88, 3),
    (ip("files-01"), 445, 5), (ip("intranet-01"), 443, 3), (ip("intranet-01"), 80, 0.5),
]
FIRST_NAMES = ["a", "c", "e", "f", "j", "l", "m", "n", "p", "s", "t", "v"]
LAST_NAMES = [
    "martin", "bernard", "dubois", "thomas", "robert", "richard", "petit", "durand", "leroy", "moreau",
    "simon", "laurent", "lefebvre", "michel", "garcia", "david", "bertrand", "roux", "vincent", "fournier",
    "morel", "girard", "andre", "mercier", "dupont", "lambert", "bonnet", "francois", "martinez", "legrand",
]
INTERFACE_ROLES = {"wan1": "wan", "dmz": "dmz", "lan": "lan", "servers": "lan", "root": "undefined"}

# Traffic mix (share of a typical week): outbound browsing, DNS, internal, shop visitors,
# web servers to the database, noise
MIX = {"outbound": 0.38, "dns": 0.07, "internal": 0.15, "web": 0.18, "database": 0.10, "noise": 0.12}
# Very short database connections per second from the failing web server during an
# apache outage (same rate as the "too many clients" lines of the postgresql dataset).
DATABASE_REFUSED_RATE = 1.0


def country_geo(country):
    iso, name, continent, lat, lon, _ = country
    return {"country_iso_code": iso, "country_name": name, "continent_name": continent,
            "location": {"lat": lat, "lon": lon}}


def ephemeral_port(rng):
    return rng.randint(49152, 65535)


class Firewall:
    name = "firewall"
    data_stream = DATA_STREAM
    template_name = "easy-elk-firewall-traffic"

    def __init__(self, seed, utc_offset_hours=1, rate=1.0):
        self.seed = seed
        self.base_rate = rate
        self.utc_offset = int(utc_offset_hours * 3600)
        # Identity of the firewall (FirewallBranch changes it).
        self.fw_name, self.fw_serial, self.wan_ip = FW_NAME, FW_SERIAL, WAN_IP
        self.interface_roles = INTERFACE_ROLES
        # Same visitors, same daily pattern and same web scanner as the apache dataset.
        self.apache = Apache(seed=seed, utc_offset_hours=utc_offset_hours, rate=rate)
        rng = random.Random(f"{seed}:{self.name}:reference")

        hosts = rng.sample(range(10, 251), 120)
        self.workstations = [
            {"ip": f"10.10.20.{h}", "user": f"{rng.choice(FIRST_NAMES)}.{rng.choice(LAST_NAMES)}"} for h in hosts
        ]
        self.workstation_pick = Weighted(self.workstations, [rng.uniform(0.3, 2.0) for _ in hosts])

        destinations, weights = [], []
        for org, iso, country, continent, lat, lon, prefixes, weight in ONLINE_SERVICES:
            geo = {"country_iso_code": iso, "country_name": country, "continent_name": continent,
                   "location": {"lat": lat, "lon": lon}}
            for _ in range(20):
                ip = f"{rng.choice(prefixes)}.{rng.randint(0, 255)}.{rng.randint(1, 254)}"
                destinations.append({"ip": ip, "org": org, "geo": geo})
                weights.append(weight / 20)
        self.destinations = Weighted(destinations, weights)

        country_pick = Weighted(COUNTRIES, [c[5] for c in COUNTRIES])
        scanners, weights = [], []
        for _ in range(1500):
            scanners.append({"ip": random_public_ip(rng), "geo": country_geo(country_pick.pick(rng))})
            weights.append(min(rng.paretovariate(1.2), 80))
        self.scanners = Weighted(scanners, weights)
        self.country_pick = country_pick
        self.blocked = [{"ip": random_public_ip(rng), "geo": country_geo(country_pick.pick(rng))} for _ in range(40)]

        self.noise_ports = Weighted([p for p, _ in NOISE_PORTS], [w for _, w in NOISE_PORTS])
        self.internal = Weighted([(ip, port) for ip, port, _ in INTERNAL_SERVICES], [w for *_, w in INTERNAL_SERVICES])
        self.web_hosts = Weighted(list(WEB_SERVERS), WEB_WEIGHTS)

        # Normalize so that the average rate over a week matches DATAGEN_RATE.
        samples = [self.mix(s) for s in range(0, 7 * 86400, 900)]
        self.mean = sum(sum(m.values()) for m in samples) / len(samples)

    # ------------------------------------------------------------ Elasticsearch / Kibana objects

    def index_template(self):
        endpoint = {"properties": {
            "ip": {"type": "ip"},
            "port": {"type": "long"},
            "bytes": {"type": "long"},
            "packets": {"type": "long"},
            "geo": {"properties": {"location": {"type": "geo_point"}}},
        }}
        source = {"properties": {**endpoint["properties"], "nat": {"properties": {
            "ip": {"type": "ip"}, "port": {"type": "long"}}}}}
        return {
            "index_patterns": [DATA_STREAM],
            "data_stream": {},
            "priority": 500,
            "_meta": {"description": "Firewall traffic logs generated by easy-elk-lab datagen"},
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
                        "event": {"properties": {"duration": {"type": "long"}}},
                        "source": source,
                        "destination": endpoint,
                        "network": {"properties": {"bytes": {"type": "long"}, "packets": {"type": "long"}}},
                    },
                },
            },
        }

    def data_view(self):
        return {
            "id": "easy-elk-firewall-traffic",
            "title": DATA_STREAM,
            "name": "Firewall logs",
            "timeFieldName": "@timestamp",
        }

    # ------------------------------------------------------------ traffic shape

    @staticmethod
    def office_hours(hour, weekday):
        if weekday >= 5:
            return 0.05
        if 8 <= hour < 12 or 13.5 <= hour < 18.5:
            return 1.0
        if 12 <= hour < 13.5:
            return 0.5
        if 7 <= hour < 8 or 18.5 <= hour < 20:
            return 0.35
        return 0.05

    def mix(self, second):
        """Relative rate of each kind of traffic at this second (before normalization)."""
        local = second + self.utc_offset
        hour = (local % 86400) / 3600
        weekday = (local // 86400 + 3) % 7  # 0 = Monday
        office = self.office_hours(hour, weekday)
        return {
            "outbound": MIX["outbound"] * office,
            "dns": MIX["dns"] * (0.3 + 0.7 * office),
            "internal": MIX["internal"] * office,
            "web": MIX["web"] * self.apache.base_factor(second),
            "database": MIX["database"] * self.apache.base_factor(second),
            "noise": MIX["noise"],
        }

    @lru_cache(maxsize=64)
    def incidents(self, day):
        """Incidents of a given UTC day (day = epoch seconds // 86400)."""
        rng = random.Random(f"{self.seed}:{self.name}:incidents:{day}")
        base = day * 86400
        result = {"portscan": None, "beaconing": None, "exfiltration": None}
        if rng.random() < 0.5:
            start = base + rng.randint(0, 23 * 3600)
            result["portscan"] = {
                "start": start,
                "end": start + rng.randint(2, 10) * 60,
                "source": {"ip": random_public_ip(rng), "geo": country_geo(self.country_pick.pick(rng))},
                "rate": 5.0,
            }
        if rng.random() < 0.15:
            start = base + rng.randint(8 * 3600, 17 * 3600) - self.utc_offset
            result["beaconing"] = {
                "start": start,
                "end": base + 86400,
                "workstation": rng.choice(self.workstations),
                "server": {"ip": random_public_ip(rng), "geo": country_geo(self.country_pick.pick(rng))},
                "port": rng.choice((443, 8443)),
                "period": 60,
            }
        if rng.random() < 0.12:
            start = base + rng.randint(1 * 3600, 4 * 3600) - self.utc_offset
            result["exfiltration"] = {
                "start": start,
                "end": start + rng.randint(30, 90) * 60,
                "workstation": rng.choice(self.workstations),
                "server": {"ip": random_public_ip(rng), "geo": country_geo(self.country_pick.pick(rng))},
                "rate": 0.05,
            }
        return result

    def active(self, kind, second):
        # Incidents of the previous day can spill over midnight UTC (local time offset).
        for day in (second // 86400, second // 86400 - 1):
            incident = self.incidents(day)[kind]
            if incident and incident["start"] <= second < incident["end"]:
                return incident
        return None

    def rates(self, second):
        """Events per second by kind: normal traffic scales with DATAGEN_RATE, incidents do not."""
        rates = {kind: self.base_rate * value / self.mean for kind, value in self.mix(second).items()}
        portscan = self.active("portscan", second)
        if portscan:
            rates["portscan"] = portscan["rate"]
        exfiltration = self.active("exfiltration", second)
        if exfiltration:
            rates["exfiltration"] = exfiltration["rate"]
        if self.apache.active("scanner", second):
            rates["web_scanner"] = 0.5
        if self.apache.active("outage", second):
            rates["database_refused"] = DATABASE_REFUSED_RATE
        return rates

    def rate(self, second):
        return sum(self.rates(second).values())

    # ------------------------------------------------------------ events

    def event(self, rng, ts):
        rates = self.rates(int(ts))
        kinds = list(rates)
        kind = Weighted(kinds, [rates[k] for k in kinds]).pick(rng)
        return getattr(self, f"session_{kind}")(rng, ts)

    def scheduled(self, rng, second):
        """Beacons: one connection about every minute, with a few seconds of jitter."""
        beacon = self.active("beaconing", second)
        if not beacon:
            return []
        k = round((second - beacon["start"]) / beacon["period"])
        jitter = random.Random(f"{self.seed}:{self.name}:beacon:{beacon['start']}:{k}").randint(-5, 5)
        if beacon["start"] + k * beacon["period"] + jitter != second:
            return []
        return [self.session_beacon(rng, second + rng.random(), beacon)]

    def session_outbound(self, rng, ts):
        ws = self.workstation_pick.pick(rng)
        if rng.random() < 0.01:
            dst = rng.choice(self.blocked)
            return self.session(rng, ts, src=ws, dst=dst, dst_port=443, in_if="lan", out_if="wan1",
                                policy=POLICY_BLOCK_MALICIOUS, direction="outbound", denied=True)
        dst = self.destinations.pick(rng)
        port = 443 if rng.random() < 0.92 else 80
        return self.session(rng, ts, src=ws, dst=dst, dst_port=port, in_if="lan", out_if="wan1",
                            policy=POLICY_LAN_INTERNET, direction="outbound", nat=True,
                            sent=rng.lognormvariate(8.5, 1.0), rcvd=rng.lognormvariate(11, 1.5),
                            duration=rng.lognormvariate(2.5, 1.3))

    def session_dns(self, rng, ts):
        ip, org, iso, country, continent, lat, lon = rng.choice(DNS_RESOLVERS)
        dst = {"ip": ip, "org": org, "geo": {"country_iso_code": iso, "country_name": country,
                                             "continent_name": continent, "location": {"lat": lat, "lon": lon}}}
        return self.session(rng, ts, src={"ip": DNS_SERVER}, dst=dst, dst_port=53, in_if="servers", out_if="wan1",
                            policy=POLICY_DNS_INTERNET, direction="outbound", nat=True,
                            sent=rng.randint(60, 90), rcvd=rng.randint(80, 400), duration=rng.random())

    def session_internal(self, rng, ts):
        ws = self.workstation_pick.pick(rng)
        ip, port = self.internal.pick(rng)
        big = port == 445
        return self.session(rng, ts, src=ws, dst={"ip": ip}, dst_port=port, in_if="lan", out_if="servers",
                            policy=POLICY_LAN_SERVERS, direction="internal",
                            sent=rng.lognormvariate(9 if big else 7, 1.5), rcvd=rng.lognormvariate(12 if big else 8, 2),
                            duration=rng.lognormvariate(2 if big else 0.5, 1.2))

    def session_web(self, rng, ts, client=None):
        if client is None:
            visitor = self.apache.clients.pick(rng)
            # The firewall only sees the IP, not the shop account of the visitor.
            client = {"ip": visitor["ip"], "geo": visitor["geo"]}
        host = self.web_hosts.pick(rng)
        port = 443 if rng.random() < 0.95 else 80
        return self.session(rng, ts, src=client, dst={"ip": WEB_SERVERS[host]}, dst_port=port,
                            in_if="wan1", out_if="dmz", policy=POLICY_INTERNET_WEB, direction="inbound",
                            sent=rng.randint(600, 4000), rcvd=rng.lognormvariate(10.5, 1.0),
                            duration=rng.lognormvariate(1.5, 1.0))

    def session_database(self, rng, ts):
        host = self.web_hosts.pick(rng)
        return self.session(rng, ts, src={"ip": WEB_SERVERS[host]}, dst={"ip": DB_SERVER}, dst_port=DB_PORT,
                            in_if="dmz", out_if="servers", policy=POLICY_DMZ_DATABASE, direction="internal",
                            sent=rng.lognormvariate(8, 1.2), rcvd=rng.lognormvariate(10, 1.5),
                            duration=rng.lognormvariate(2, 1.2))

    def session_database_refused(self, rng, ts):
        """The database accepts the TCP connection, then closes it at once (no slot left)."""
        host = self.apache.active("outage", int(ts))["host"]
        return self.session(rng, ts, src={"ip": WEB_SERVERS[host]}, dst={"ip": DB_SERVER}, dst_port=DB_PORT,
                            in_if="dmz", out_if="servers", policy=POLICY_DMZ_DATABASE, direction="internal",
                            sent=rng.randint(80, 120), rcvd=rng.randint(90, 130), duration=0, action="close")

    def session_web_scanner(self, rng, ts):
        scanner = self.apache.active("scanner", int(ts))
        return self.session_web(rng, ts, client={"ip": scanner["ip"], "geo": scanner["geo"]})

    def session_noise(self, rng, ts):
        port = self.noise_ports.pick(rng)
        return self.session(rng, ts, src=self.scanners.pick(rng), dst={"ip": self.wan_ip}, dst_port=port,
                            in_if="wan1", out_if="root", policy=POLICY_IMPLICIT_DENY, direction="inbound",
                            denied=True)

    def session_portscan(self, rng, ts):
        scan = self.active("portscan", int(ts))
        port = rng.randint(1, 1024) if rng.random() < 0.7 else self.noise_ports.pick(rng)
        return self.session(rng, ts, src=scan["source"], dst={"ip": self.wan_ip}, dst_port=port,
                            in_if="wan1", out_if="root", policy=POLICY_IMPLICIT_DENY, direction="inbound",
                            denied=True)

    def session_exfiltration(self, rng, ts):
        exfil = self.active("exfiltration", int(ts))
        return self.session(rng, ts, src=exfil["workstation"], dst=exfil["server"], dst_port=443,
                            in_if="lan", out_if="wan1", policy=POLICY_LAN_INTERNET, direction="outbound", nat=True,
                            sent=rng.lognormvariate(17.5, 0.8), rcvd=rng.randint(4000, 9000),
                            duration=rng.uniform(20, 120))

    def session_beacon(self, rng, ts, beacon):
        return self.session(rng, ts, src=beacon["workstation"], dst=beacon["server"], dst_port=beacon["port"],
                            in_if="lan", out_if="wan1", policy=POLICY_LAN_INTERNET, direction="outbound", nat=True,
                            sent=rng.randint(300, 700), rcvd=rng.randint(200, 400), duration=rng.uniform(0, 2))

    # ------------------------------------------------------------ document

    def session(self, rng, ts, src, dst, dst_port, in_if, out_if, policy, direction,
                denied=False, nat=False, sent=0, rcvd=0, duration=0, action=None):
        service, transport, protocol = SERVICES.get(dst_port, (f"tcp/{dst_port}", "tcp", None))
        src_port = ephemeral_port(rng)
        session_id = rng.randint(10_000_000, 999_999_999)
        duration = int(duration)
        if denied:
            sent = rcvd = 0
            sent_pkt, rcvd_pkt = 1, 0
            action = "deny"
            nat = False
        else:
            sent, rcvd = int(sent), int(rcvd)
            sent_pkt = max(1, sent // rng.randint(500, 1400))
            rcvd_pkt = max(1, rcvd // rng.randint(800, 1400))
            if action:
                pass
            elif transport == "udp":
                action = "accept"
            else:
                draw = rng.random()
                action = "close" if draw < 0.9 else ("client-rst" if draw < 0.95 else
                                                     ("server-rst" if draw < 0.98 else "timeout"))
        nat_port = ephemeral_port(rng) if nat else None
        local = out_if == "root"
        user = src.get("user")

        # ---- raw FortiGate line
        fields = [
            ("date", time.strftime("%Y-%m-%d", time.gmtime(ts))),
            ("time", time.strftime("%H:%M:%S", time.gmtime(ts))),
            ("devname", f'"{self.fw_name}"'), ("devid", f'"{self.fw_serial}"'),
            ("eventtime", int(ts * 1_000_000_000)), ("tz", '"+0000"'),
            ("logid", '"0001000014"' if local else '"0000000013"'),
            ("type", '"traffic"'), ("subtype", '"local"' if local else '"forward"'),
            ("level", '"warning"' if denied else '"notice"'), ("vd", '"root"'),
            ("srcip", src["ip"]), ("srcport", src_port),
            ("srcintf", f'"{in_if}"'), ("srcintfrole", f'"{self.interface_roles[in_if]}"'),
            ("dstip", dst["ip"]), ("dstport", dst_port),
            ("dstintf", f'"{out_if}"'), ("dstintfrole", f'"{self.interface_roles[out_if]}"'),
            ("srccountry", f'"{src["geo"]["country_name"] if "geo" in src else "Reserved"}"'),
            ("dstcountry", f'"{dst["geo"]["country_name"] if "geo" in dst else "Reserved"}"'),
            ("sessionid", session_id), ("proto", PROTO_NUMBERS[transport]), ("action", f'"{action}"'),
            ("policyid", policy[0]), ("policytype", '"policy"'), ("policyname", f'"{policy[1]}"'),
        ]
        if user:
            fields.append(("user", f'"{user}"'))
        fields.append(("service", f'"{service}"'))
        if nat:
            fields += [("trandisp", '"snat"'), ("transip", self.wan_ip), ("transport", nat_port)]
        else:
            fields.append(("trandisp", '"noop"'))
        fields += [("duration", duration), ("sentbyte", sent), ("rcvdbyte", rcvd), ("sentpkt", sent_pkt)]
        if not denied:
            fields.append(("rcvdpkt", rcvd_pkt))
        message = " ".join(f"{key}={value}" for key, value in fields)

        # ---- ECS document
        source = {"ip": src["ip"], "port": src_port, "bytes": sent, "packets": sent_pkt}
        if "geo" in src:
            source["geo"] = src["geo"]
        if nat:
            source["nat"] = {"ip": self.wan_ip, "port": nat_port}
        destination = {"ip": dst["ip"], "port": dst_port, "bytes": rcvd, "packets": rcvd_pkt}
        if "geo" in dst:
            destination["geo"] = dst["geo"]
        if "org" in dst:
            destination["as"] = {"organization": {"name": dst["org"]}}
        network = {"transport": transport, "iana_number": str(PROTO_NUMBERS[transport]),
                   "direction": direction, "bytes": sent + rcvd, "packets": sent_pkt + rcvd_pkt}
        if protocol:
            network["protocol"] = protocol

        doc = {
            "@timestamp": datetime.fromtimestamp(ts, timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z"),
            "message": message,
            "data_stream": {"type": "logs", "dataset": "firewall.traffic", "namespace": "lab"},
            "event": {
                "dataset": "firewall.traffic",
                "kind": "event",
                "category": ["network"],
                "type": ["connection", "denied"] if denied else ["connection", "allowed", "end"],
                "action": action,
                "outcome": "failure" if denied else "success",
                "duration": duration * 1_000_000_000,
            },
            "observer": {
                "name": self.fw_name, "vendor": "Fortinet", "product": "FortiGate", "type": "firewall",
                "serial_number": self.fw_serial,
                "ingress": {"interface": {"name": in_if}},
                "egress": {"interface": {"name": out_if}},
            },
            "source": source,
            "destination": destination,
            "network": network,
            "rule": {"id": str(policy[0]), "name": policy[1]},
        }
        if user:
            doc["user"] = {"name": user}
            doc["source"]["user"] = {"name": user}
        return doc


class FirewallBranch(Firewall):
    """fw-02, firewall of the Lyon branch office (not in company.SERVERS, no incidents).

    About 25 employees: Internet browsing, access to the head office servers through an
    IPsec tunnel (interface "to-hq"), Internet noise on its public IP. Its own name in the
    rng keys, so its sessions never duplicate those of fw-01. Used by `datagen.py syslog`.
    """

    name = "firewall-fw-02"

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.fw_name, self.fw_serial, self.wan_ip = "fw-02", "FG60FTK24001388", "198.51.100.20"
        self.interface_roles = {**INTERFACE_ROLES, "to-hq": "wan"}
        rng = random.Random(f"{self.seed}:{self.name}:office")
        hosts = rng.sample(range(10, 251), 25)
        self.workstations = [
            {"ip": f"10.20.20.{h}", "user": f"{rng.choice(FIRST_NAMES)}.{rng.choice(LAST_NAMES)}"} for h in hosts
        ]
        self.workstation_pick = Weighted(self.workstations, [rng.uniform(0.3, 2.0) for _ in hosts])

    def mix(self, second):
        local = second + self.utc_offset
        office = self.office_hours((local % 86400) / 3600, (local // 86400 + 3) % 7)
        return {"outbound": 0.55 * office, "internal": 0.25 * office, "noise": 0.20}

    def rates(self, second):
        return {kind: self.base_rate * value / self.mean for kind, value in self.mix(second).items()}

    def active(self, kind, second):
        return None

    def session_internal(self, rng, ts):
        ws = self.workstation_pick.pick(rng)
        ip, port = self.internal.pick(rng)
        big = port == 445
        return self.session(rng, ts, src=ws, dst={"ip": ip}, dst_port=port, in_if="lan", out_if="to-hq",
                            policy=(7, "Lyon-to-HQ"), direction="outbound",
                            sent=rng.lognormvariate(9 if big else 7, 1.5), rcvd=rng.lognormvariate(12 if big else 8, 2),
                            duration=rng.lognormvariate(2 if big else 0.5, 1.2))
