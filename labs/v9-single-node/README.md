# Lab: Elastic Stack 9 — single node

One Elasticsearch node and one Kibana, Elastic Stack 9.5.5, with test data loaded on
demand (web server, firewall and database logs, host metrics: 7 days of history, then
live).

> **Lab environment only.** Security is enabled (users and passwords), but HTTP is
> not encrypted. Do not use this setup in production.

## Prerequisites

- Linux machine with Docker and the `docker compose` plugin, and your user allowed to
  run `docker` commands
- About 6 GB of free RAM (Elasticsearch: 2 GB heap, Kibana: 1 GB heap)
- `vm.max_map_count` set to at least 262144:

  ```bash
  sudo sysctl -w vm.max_map_count=262144
  ```

`./lab.sh check` verifies all of this for you.

## Quick start

```bash
cd labs/v9-single-node
./lab.sh start        # Elasticsearch + Kibana
./lab.sh data load    # test data, with a progress bar per dataset
```

The first start creates a `.env` file with random passwords, checks the prerequisites,
then starts the lab. Kibana can take a couple of minutes. At the end, the script prints
the URLs and the password of the `elastic` user. Show them again at any time with:

```bash
./lab.sh info
```

## Test data

Test data is not loaded when the lab starts: load it when you need it.

```bash
./lab.sh data load
```

```
Loading test data (7 days: apache,firewall,metrics,postgresql)
  From 2026-10-05 15:02 UTC to now

  apache      [███████████████░░░░░░░░░░░░░░░]  52%      35120 docs
  firewall    [██████████████░░░░░░░░░░░░░░░░]  49%      33870 docs
  metrics     [███████████████░░░░░░░░░░░░░░░]  51%     108040 docs
  postgresql  [███████████████░░░░░░░░░░░░░░░]  52%      36200 docs
```

All datasets load at the same time (about a minute for 7 days). Then new data keeps
being generated in real time, and it restarts with the lab (`./lab.sh start`
catches up on the time the lab was stopped).

- Interrupted with Ctrl-C? Run `./lab.sh data load` again: it resumes where it stopped.
- More history, or only some datasets:
  `./lab.sh data load --days 30`, `./lab.sh data load --datasets apache,metrics`.

The datasets:

- Apache web server logs: data view **Web logs (Apache)**
- Firewall logs: data view **Firewall logs**
- PostgreSQL database logs: data view **Database logs**
- Server metrics (CPU, memory, disk, network): data view **Host metrics**, and
  **Observability > Infrastructure** in Kibana

In Kibana, open **Discover** and select one of these data views.

```bash
./lab.sh data status     # what has been generated
./lab.sh data logs       # follow the live generator
./lab.sh data stop       # stop live generation (data is kept)
```

What the data contains, and all generator settings:
[datagen/README.md](../../datagen/README.md).

## Fleet Server (optional)

Some labs of the Elastic Stack course use Elastic Agent managed by Fleet. Fleet Server
is not started with the lab; start it when a lab asks for it (about 1 GB more memory):

```bash
./lab.sh fleet start     # prepares Fleet in Kibana, then starts Fleet Server
./lab.sh fleet status    # Fleet Server container and number of agents
./lab.sh fleet stop      # stop it (enrolled agents and settings are kept)
```

Then open **Kibana > Management > Fleet**: the Fleet Server appears in **Agents**.
Once started, it restarts with the lab (`./lab.sh start`).

Agents running on the lab network enroll with `--url=https://fleet-server:8220
--insecure`. Fleet Server uses a self-signed certificate, which is why `--insecure` is
needed: in production, agents trust the certificate authority instead.

## Install Elastic Agent on a server (optional)

The lab provides a bare Linux server, `web-01` (Debian), on the lab network. You install
Elastic Agent on it yourself, as on a real server:

```bash
./lab.sh fleet start          # Fleet Server must be running
./lab.sh agent shell web-01   # shell as user 'student' (sudo allowed)
```

The first time, this builds a small image (about 20 seconds). Then, in Kibana:
**Fleet > Add agent**, pick or create an agent policy, choose **Linux Tar**, and paste the
commands into the shell. Add `--insecure` at the end of the `elastic-agent install`
line (Fleet Server uses a self-signed certificate). The download is about 430 MB.

- Apple Silicon Mac: replace `x86_64` with `arm64` in the commands.
- Forgot `--insecure`? The agent is installed but cannot enroll. Remove it with
  `sudo elastic-agent uninstall`, then run the install command again with `--insecure`.
- There is no systemd in the container: the agent runs as an init.d service, which the
  server starts and stops with the lab. Check it with `sudo elastic-agent status`.

```bash
./lab.sh agent status         # servers and Elastic Agent status
./lab.sh agent stop web-01    # stop the server (what is installed is kept)
./lab.sh agent reset web-01   # delete the server and start again from scratch
```

After `reset`, the old agent stays in Fleet as offline: unenroll it from Fleet.

## Commands

| Command                            | What it does                                          |
|------------------------------------|-------------------------------------------------------|
| `./lab.sh start`                   | Create `.env` if needed, check, start the lab (and live data, if loaded) |
| `./lab.sh data load [--days N] [--datasets a,b]` | Load test data with progress bars, then go live |
| `./lab.sh stop`                    | Stop the lab (data is kept)                           |
| `./lab.sh restart`                 | Stop then start                                       |
| `./lab.sh status`                  | Show containers and cluster health                    |
| `./lab.sh info`                    | Show URLs and credentials                             |
| `./lab.sh logs [es01\|kibana\|fleet-server]` | Follow logs                                |
| `./lab.sh es <METHOD> <path> [body]` | Send an authenticated request to Elasticsearch      |
| `./lab.sh data [status\|start\|stop\|logs\|backfill\|reset]` | Manage test data (see `./lab.sh help`) |
| `./lab.sh fleet [start\|status\|stop\|logs]` | Fleet Server, started only on demand            |
| `./lab.sh agent [shell\|status\|stop\|reset] [server]` | Bare servers to install Elastic Agent on |
| `./lab.sh check`                   | Check prerequisites only                              |
| `./lab.sh destroy`                 | Remove the lab and **all its data** (asks to confirm) |

Examples of requests:

```bash
./lab.sh es GET /
./lab.sh es GET '/_cat/indices?v'
./lab.sh es PUT /my-index '{"settings":{"number_of_replicas":0}}'
```

## Configuration

Settings live in `.env` (created from `.env.example`): stack version, ports, memory,
passwords, test data. After changing it, run `./lab.sh restart`.

Test data settings in `.env` (`DATAGEN_*`) are the defaults of `./lab.sh data load`.

## Troubleshooting

- **Kibana says a field was not found** (for example `system.cpu.total.norm.pct` in
  Observability > Infrastructure) right after the first start: reload the page
  (Ctrl+Shift+R). Your browser kept the list of fields from before the data existed.

Changing the passwords only works on a fresh lab: run `./lab.sh destroy` first.
