"""The fictional company behind every dataset, described once.

An online shop (shop.example.com) run by a small company. All datasets read their
servers, IP addresses and traffic split from here, so the logs and metrics of the
different sources always agree (same names, same IPs, same load).

Network (firewall fw-01):
  wan1     203.0.113.10       public IP (source NAT for outbound traffic)
  dmz      172.16.10.0/24     the shop web servers
  servers  10.10.10.0/24      internal servers
  lan      10.10.20.0/24      employee workstations

Changing a value here changes the generated data of several datasets: participants
then need `./lab.sh data reset`.
"""

DOMAIN = "shop.example.com"
WAN_IP = "203.0.113.10"
DB_NAME = "shop"
DB_PORT = 5432

UBUNTU = {
    "name": "Ubuntu",
    "version": "24.04.3 LTS (Noble Numbat)",
    "family": "debian",
    "platform": "ubuntu",
    "kernel": "6.8.0-85-generic",
    "type": "linux",
}

# Servers, in a fixed order (datasets iterate over them: keep the order stable).
#   zone           network zone behind the firewall
#   role           web, db, directory, files, intranet, dns
#   traffic_share  share of the shop traffic (web servers only)
#   cores, memory_gib, disks: hardware, for servers that report metrics
#   disks          (mount point, device, size GiB, base used share, weekly growth share)
SERVERS = {
    "web-01": {"ip": "172.16.10.11", "zone": "dmz", "role": "web", "traffic_share": 0.4,
               "cores": 4, "memory_gib": 8, "disks": [("/", "/dev/sda1", 80, 0.32, 0.18)]},
    "web-02": {"ip": "172.16.10.12", "zone": "dmz", "role": "web", "traffic_share": 0.4,
               "cores": 4, "memory_gib": 8, "disks": [("/", "/dev/sda1", 80, 0.34, 0.18)]},
    "web-03": {"ip": "172.16.10.13", "zone": "dmz", "role": "web", "traffic_share": 0.2,
               "cores": 4, "memory_gib": 8, "disks": [("/", "/dev/sda1", 80, 0.30, 0.12)]},
    "db-01": {"ip": "10.10.10.60", "zone": "servers", "role": "db",
              "cores": 8, "memory_gib": 32, "disks": [("/", "/dev/sda1", 60, 0.28, 0.05),
                                                      ("/var/lib/postgresql", "/dev/sdb1", 500, 0.55, 0.10)]},
    "dc-01": {"ip": "10.10.10.10", "zone": "servers", "role": "directory"},
    "files-01": {"ip": "10.10.10.20", "zone": "servers", "role": "files"},
    "intranet-01": {"ip": "10.10.10.30", "zone": "servers", "role": "intranet"},  # also runs the nightly reporting
    "dns-01": {"ip": "10.10.10.53", "zone": "servers", "role": "dns"},
}

WEB_SERVERS = [name for name, server in SERVERS.items() if server["role"] == "web"]
WEB_WEIGHTS = [SERVERS[name]["traffic_share"] for name in WEB_SERVERS]
# Servers that report host metrics (the metrics dataset).
MONITORED_SERVERS = [name for name, server in SERVERS.items() if "cores" in server]


def ip(name):
    return SERVERS[name]["ip"]
