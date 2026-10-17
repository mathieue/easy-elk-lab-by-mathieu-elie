# Test data generator

Generates realistic test data into Elasticsearch, so labs have something to explore
from the first minute.

- **History, then live**: at first start it loads the last days of data (7 by default),
  then keeps generating new events in real time.
- **Same data for everyone**: data only depends on the time and the seed. Two labs with
  the same seed contain exactly the same events, including incidents.
- **No duplicates**: every event has a fixed id. Restarting the generator, or loading
  a time range twice, never duplicates data.

Nothing is loaded when a lab starts. Use it through the lab script:

```bash
./lab.sh data load                    # load history (progress bars), then go live
./lab.sh data load --days 30          # more history (existing data is kept)
./lab.sh data load --datasets apache  # only some datasets
./lab.sh data status                  # what has been generated
./lab.sh data logs                    # follow the generator
./lab.sh data stop                    # stop live generation (data is kept)
./lab.sh data start                   # start it again (fills the gap first)
                                      # (also restarted by ./lab.sh start)
./lab.sh data backfill now-7d now+3d  # load a fixed range, including the future
./lab.sh data reset                   # delete generated data and load it again
```

The generator also writes **raw log files** on the agent servers of the lab
(`./lab.sh agent shell web-04`): `web-04` is an extra web server of the shop, and its
Apache access log `/var/log/apache2/access.log` gets the last
`DATAGEN_FILE_HISTORY_HOURS` (24) hours, then live lines (command
`datagen.py file --host web-04 --path ...`, started by the server itself). Same
visitors and daily rhythm as the `apache` dataset, but its own requests (never the
same as on web-01..03), normal traffic only (no incidents). It resumes after the last
line of the file, so a restart never duplicates lines.

## Datasets

### `apache` — web server access logs

Apache access logs (combined log format) of a fictional online shop
(`shop.example.com`) served by three web servers (`web-01`, `web-02`, `web-03`).

- Data stream: `logs-apache.access-lab`
- Kibana data view: **Web logs (Apache)**
- Fields follow the Elastic Common Schema (ECS): `source.ip`, `source.geo.*`,
  `http.request.method`, `http.response.status_code`, `http.response.body.bytes`,
  `url.*`, `user_agent.*`, `user.name`, `host.name`. The raw log line is in `message`.

What you will find in the data:

- Daily pattern: quiet at night, busy late morning, early afternoon and evening.
  Less traffic on weekends.
- Visitors mostly from France, plus Europe, Africa, the Americas and Asia.
- Desktop and mobile browsers, and search engine crawlers.
- Usual errors: missing pages (404), rare server errors (500).
- Incidents, on some days only:
  - **outage**: one web server returns many 5xx errors for 10 to 45 minutes (about
    300 errors per 10 minutes, whatever `DATAGEN_RATE` is);
  - **scanner**: one IP probes for vulnerable URLs (`/wp-login.php`, `/.env`, ...)
    for 5 to 20 minutes, causing a burst of 404 and 403.

### `firewall` — network firewall traffic logs

Traffic logs of the firewall `fw-01` (FortiGate `key=value` format) that protects the
company running the shop above.

- Data stream: `logs-firewall.traffic-lab`
- Kibana data view: **Firewall logs**
- Fields follow ECS: `source.*` / `destination.*` (ip, port, bytes, packets, geo),
  `source.nat.*`, `destination.as.organization.name`, `network.*` (transport,
  protocol, direction, bytes), `event.action` (`close`, `deny`, ...), `rule.name`,
  `observer.*`, `user.name`. The raw log line is in `message`.

Network behind the firewall:

| Interface | Addresses          | What                                           |
|-----------|--------------------|------------------------------------------------|
| `wan1`    | `203.0.113.10`     | Public IP, source NAT for outbound traffic     |
| `dmz`     | `172.16.10.11-13`  | `web-01`, `web-02`, `web-03`: the shop         |
| `lan`     | `10.10.20.0/24`    | Employee workstations (with user names)        |
| `servers` | `10.10.10.0/24`    | DNS, Active Directory, file server, intranet, `db-01` (shop database) |

What you will find in the data:

- Employees browsing the Internet and using internal servers, mostly during office
  hours on weekdays.
- Shop visitors reaching the web servers: same IPs and same daily pattern as the
  `apache` dataset.
- Web servers querying the database `db-01` (port 5432). During an `apache` outage,
  the failing web server opens hundreds of very short connections to the database:
  the same moment as the `too many clients` errors of the `postgresql` dataset.
- Internet background noise: constant connection attempts on SSH, Telnet, RDP, SMB,
  databases, ... all denied.
- Incidents, on some days only:
  - **portscan**: one Internet IP probes hundreds of ports in a few minutes;
  - **beaconing**: an infected workstation contacts an unknown server about every
    minute (small, regular connections) until the end of the day;
  - **exfiltration**: at night, a workstation uploads gigabytes to an unknown server;
  - the **web scanner** of the `apache` dataset also appears here, same IP, same time.

### `metrics` — host metrics

Metrics of the shop servers, like the Metricbeat `system` module: one document per
metricset (`cpu`, `memory`, `load`, `network`, `filesystem`) per server, every minute.

- Servers: `web-01`, `web-02`, `web-03` (4 cores, 8 GB) and `db-01` (8 cores, 32 GB,
  database disk `/var/lib/postgresql`), Ubuntu 24.04.
- Data stream: `metrics-system.host-lab`
- Kibana data view: **Host metrics**. The servers also show up in
  **Observability > Infrastructure** (inventory and hosts views).
- Fields: `host.*` (name, ip, os, `host.cpu.usage`, `host.network.ingress/egress.*`),
  `system.cpu.*`, `system.memory.*`, `system.load.*`, `system.filesystem.*`,
  `metricset.name`, `event.dataset` (`system.cpu`, ...).

What you will find in the data:

- CPU, memory and network follow the shop traffic: low at night, busy during the day.
  `web-03` handles half the traffic of the two other web servers.
- During an **outage** of the `apache` dataset, the failing web server is saturated
  (CPU above 90 %) and `db-01` is heavily loaded (CPU around 75 %, more I/O wait).
- Disks fill up during the week and are cleaned up every Sunday night.

The number of metrics does not depend on `DATAGEN_RATE`: it is one report per server
every `DATAGEN_METRICS_INTERVAL` seconds (60 by default), about 30,000 documents per
day.

### `postgresql` — database logs

Logs of the shop database server `db-01` (PostgreSQL 17, database `shop`), in the
standard PostgreSQL format (`%m [%p] %q%u@%d `), with connections, disconnections,
checkpoints, lock waits and slow statements (over 500 ms) logged.

- Data stream: `logs-postgresql.log-lab`
- Kibana data view: **Database logs**
- Fields: `log.level` (`LOG`, `ERROR`, `FATAL`), `postgresql.log.*` (`database`,
  `query`, `query_duration_ms`, `sql_state_code`), `event.duration`, `user.name`,
  `client.ip` (which web server), `process.pid`, `error.*`. The raw lines (including
  `DETAIL`, `CONTEXT`, `STATEMENT`) are in `message`.

What you will find in the data:

- Web servers connecting and disconnecting, slow queries, autovacuum, a few errors
  (duplicate key, statement timeout), all following the shop traffic.
- A checkpoint every 5 minutes, and a reporting job with very long queries every
  night around 02:00.
- **The cause of the `apache` outages**: from 5 minutes before an outage, lock waits
  and very slow queries pile up; during the outage, the failing web server can no
  longer connect (`FATAL: sorry, too many clients already`). Together with the
  `metrics` dataset (database server CPU), this lets you diagnose an outage from
  the 5xx errors to the root cause.

## Settings

Set in the lab `.env` file:

| Variable               | Default        | Meaning                                              |
|------------------------|----------------|------------------------------------------------------|
| `DATAGEN_DATASETS`     | `apache,firewall,metrics,postgresql` | Datasets to generate, comma separated |
| `DATAGEN_HISTORY_DAYS` | `7`            | Days of history loaded at first start                |
| `DATAGEN_RATE`         | `0.1`          | Average events per second of normal traffic, per dataset |
| `DATAGEN_SEED`         | `easy-elk-lab` | Change it to get different data                      |
| `DATAGEN_UTC_OFFSET`   | `1`            | UTC offset (hours) used for the day/night pattern    |
| `DATAGEN_METRICS_INTERVAL` | `60`       | Seconds between two host metrics reports             |

`DATAGEN_RATE` only changes the volume of normal traffic. Incidents keep the same
intensity whatever the rate, so they stay easy to spot with light data.

Logs (`apache` and `firewall`; `postgresql` adds about the same as one of them):

| `DATAGEN_RATE` | 7 days, both datasets | 30 days, both datasets | Live, per dataset           |
|----------------|-----------------------|------------------------|-----------------------------|
| `0.1`          | ~130k events, ~30 MB  | ~580k events, ~125 MB  | ~6 events/min (more by day) |
| `1`            | ~1.2M events, ~350 MB | ~5M events, ~1.5 GB    | ~60 events/min              |

Metrics add about 210,000 documents (~35 MB) per 7 days, whatever the rate.

Need more history (machine learning, week-over-week comparisons)? Set
`DATAGEN_HISTORY_DAYS=30` and run `./lab.sh data reset`, or keep the current data and
add the older days with `./lab.sh data backfill now-30d now-7d` (same result).

Changing the rate or the seed changes the data: run `./lab.sh data reset` afterwards.
