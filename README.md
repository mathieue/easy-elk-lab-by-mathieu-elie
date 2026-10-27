# Easy ELK Lab — by Mathieu Elie

**A ready-to-use Elastic Stack 9 (ELK) training lab: Elasticsearch, Kibana, Logstash, Fleet Server and Elastic Agent, with realistic test data, started in one command.**

Created and maintained by **[Mathieu Elie](https://www.mathieu-elie.net)**, Elastic Stack (ELK) trainer and consultant.

![Elastic Stack](https://img.shields.io/badge/Elastic%20Stack-9.5.5-005571?logo=elastic)
![Docker Compose](https://img.shields.io/badge/Docker-Compose-2496ED?logo=docker&logoColor=white)
![Ubuntu](https://img.shields.io/badge/Ubuntu-24.04%20%7C%2026.04-E95420?logo=ubuntu&logoColor=white)

---

## What is Easy ELK Lab?

Easy ELK Lab is the lab environment that participants of my Elastic Stack trainings clone on day one. One `git clone`, one command, and every participant has the same working Elastic Stack, the same data and the same incidents to investigate — no time lost on installation.

It is also a good starting point for anyone who wants to **learn Elasticsearch, Kibana and Logstash on realistic data**, or test an Elastic Stack 9 feature locally.

- **One command**: `./lab.sh start` creates passwords, checks prerequisites and starts Elasticsearch + Kibana.
- **Realistic data from minute one**: web server, firewall, database logs and host metrics of a fictional online shop — 7 days of history, then live.
- **Real incidents to investigate**: web outages, vulnerability scanners, port scans, malware beaconing, data exfiltration — correlated across datasets, from the 5xx errors down to the root cause in the database.
- **Same data for everyone**: data is deterministic (seed-based), so a whole training group sees exactly the same events.
- **Everything optional, on demand**: Fleet Server, a bare Ubuntu server to install Elastic Agent by hand, Logstash with a live FortiGate syslog source.
- **Elastic Common Schema (ECS)** fields, ready-made Kibana data views, Observability > Infrastructure populated.

> **Lab environment only.** Security is enabled (users and passwords), but HTTP is not encrypted. Do not use this setup in production.

## Quick start

On a fresh Ubuntu 24.04 or 26.04 machine (8 GB RAM, 20 GB disk recommended):

```bash
git clone https://github.com/mathieue/easy-elk-lab-by-mathieu-elie.git
cd easy-elk-lab-by-mathieu-elie
sudo ./setup-ubuntu.sh        # Docker, compose plugin, vm.max_map_count, images (asks before each step)
```

Log out and back in (for the `docker` group), then:

```bash
cd labs/v9-single-node
./lab.sh start                # Elasticsearch + Kibana
./lab.sh data load            # load test data (progress bars), then go live
./lab.sh info                 # URLs and the password of the 'elastic' user
```

Open Kibana at <http://localhost:5601>, log in as `elastic`, then go to **Discover**.

Already have Docker on another Linux machine? Skip `setup-ubuntu.sh`: `./lab.sh check` verifies the prerequisites.

## What's in the lab

| Component | Started | Role in the lab |
|---|---|---|
| Elasticsearch 9.5.5 (single node) | always | Storage, search, aggregations |
| Kibana 9.5.5 | always | Discover, dashboards, Observability, Fleet |
| Test data generator | `./lab.sh data load` | 4 datasets, history + live |
| Fleet Server | `./lab.sh fleet start` | Manage Elastic Agents from Kibana |
| `web-04` bare Ubuntu server | `./lab.sh agent shell web-04` | Install Elastic Agent by hand, like on a real server |
| Logstash + `fw-02` firewall | `./lab.sh logstash start` | Write a Logstash pipeline for live FortiGate syslog |

### Test datasets

| Dataset | Kibana data view | Content |
|---|---|---|
| `apache` | Web logs (Apache) | Access logs of 3 web servers of an online shop, outages and scanners |
| `firewall` | Firewall logs | FortiGate traffic logs: employees, shop visitors, port scans, beaconing, exfiltration |
| `metrics` | Host metrics | CPU, memory, load, network, disk of web and database servers |
| `postgresql` | Database logs | Connections, slow queries, lock waits, `too many clients` — the root cause of outages |

Details of every field, incident and setting: [datagen/README.md](datagen/README.md).
All lab commands, Fleet, Elastic Agent and Logstash: [labs/v9-single-node/README.md](labs/v9-single-node/README.md).

## Repository layout

```
.
├── setup-ubuntu.sh            # prepares a fresh Ubuntu machine (Docker, sysctl, images)
├── labs/
│   └── v9-single-node/        # Elastic Stack 9 lab: compose.yml, lab.sh, .env.example
│       ├── agent-host/        # image of the bare server web-04 (Elastic Agent exercises)
│       └── logstash/          # example Logstash pipeline (fw-02)
└── datagen/                   # deterministic test data generator (Python)
```

## FAQ

**How do I get an Elasticsearch and Kibana lab running quickly?**
Clone this repository, run `sudo ./setup-ubuntu.sh` once, then `./lab.sh start` and `./lab.sh data load` in `labs/v9-single-node`. Kibana is on port 5601 with data already loaded.

**Is it free?**
Yes. The lab uses the Elastic Stack **basic** license by default (free, never expires). Set `LICENSE=trial` in `.env` to test all features for 30 days.

**How much memory does it need?**
About 6 GB of free RAM for Elasticsearch and Kibana, plus about 1 GB for each optional component (Fleet Server, Logstash, `web-04`).

**Can I use it to learn Elastic Agent and Fleet?**
Yes: `./lab.sh fleet start`, then `./lab.sh agent shell web-04` gives you a bare Ubuntu server where you install Elastic Agent from **Kibana > Fleet > Add agent**, with a real Apache log to collect.

**Can I use it to learn Logstash?**
Yes: `./lab.sh logstash start` starts Logstash and a firewall sending live FortiGate syslog on UDP 5514. Edit `logstash/pipeline/fw-02.conf`; Logstash reloads it automatically.

## Training

This lab is the hands-on environment of my Elastic Stack trainings: Elasticsearch, Kibana, Logstash, Elastic Agent and Fleet, on realistic observability and security use cases. Training and consulting for teams: [mathieu-elie.net](https://www.mathieu-elie.net).

## En français

**Easy ELK Lab** est le lab de mes formations Elastic Stack (ELK) : Elasticsearch, Kibana, Logstash, Elastic Agent et Fleet en version 9, prêts à l'emploi en une commande, avec des données de test réalistes (logs web, pare-feu, base de données, métriques serveurs) et de vrais incidents à analyser. Chaque participant obtient le même environnement et les mêmes données. Formations et conseil : [mathieu-elie.net](https://www.mathieu-elie.net).

## Author

**Mathieu Elie** — Elastic Stack (ELK) trainer and consultant
- Website: [www.mathieu-elie.net](https://www.mathieu-elie.net)
- GitHub: [@mathieue](https://github.com/mathieue)

If this lab helps you, a ⭐ on the repository is appreciated.

---

*Elasticsearch, Kibana, Logstash and Elastic are trademarks of Elasticsearch B.V. This project is not affiliated with or endorsed by Elastic.*
