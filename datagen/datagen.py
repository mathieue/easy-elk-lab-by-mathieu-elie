#!/usr/bin/env python3
"""Generate realistic test data into Elasticsearch: history first, then live.

Data is a pure function of time: for a given second, the same events (with the
same document ids) are always produced. Re-running never creates duplicates,
and a restart simply resumes where the data stops.

Commands:
  run [--existing-only]   Load history (from the latest document, or from
                          now - DATAGEN_HISTORY_DAYS), then follow live.
                          --existing-only: only datasets that already have data.
  load [--progress]       Load history for all datasets at the same time (one
                          hour of data per dataset in turn), then exit.
                          --progress prints machine-readable lines on stdout:
                            plan <first second> <last second>
                            dataset <name> <percent> <docs>   (once per dataset)
                            progress <name> <percent> <docs>
                            done <name> <docs>
  backfill FROM TO        Load a fixed time range, then exit.
                          FROM/TO: ISO date, "now", "now-30d", "now+2h", ...
  setup                   Install index templates and Kibana data views only.
"""

import argparse
import base64
import json
import math
import os
import random
import re
import signal
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone

from datasets import DATASETS

BULK_SIZE = 5000
BACKFILL_CHUNK_SECONDS = 600
PROGRESS_EVERY_SECONDS = 10
LIVE_REPORT_EVERY_SECONDS = 60
RESUME_OVERLAP_SECONDS = 60
LOAD_CHUNK_SECONDS = 3600
LOAD_PROGRESS_EVERY_SECONDS = 0.25
SEED_FIELDS_WINDOW_SECONDS = 6 * 3600

stopping = False


def log(message):
    # stderr: stdout is reserved for the machine-readable progress of `load --progress`.
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    print(f"{stamp} {message}", file=sys.stderr, flush=True)


def on_signal(signum, frame):
    global stopping
    stopping = True


# ---------------------------------------------------------------- HTTP


class Http:
    def __init__(self, url, user, password, extra_headers=None):
        self.url = url.rstrip("/")
        token = base64.b64encode(f"{user}:{password}".encode()).decode()
        self.headers = {"Authorization": f"Basic {token}", **(extra_headers or {})}

    def request(self, method, path, body=None, ndjson=False):
        """Return (status, parsed body). Raises OSError if unreachable."""
        headers = dict(self.headers)
        data = None
        if body is not None:
            if ndjson:
                data = body.encode()
                headers["Content-Type"] = "application/x-ndjson"
            else:
                data = json.dumps(body).encode()
                headers["Content-Type"] = "application/json"
        req = urllib.request.Request(self.url + path, data=data, method=method, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                return resp.status, json.loads(resp.read() or b"null")
        except urllib.error.HTTPError as err:
            raw = err.read()
            try:
                return err.code, json.loads(raw or b"null")
            except ValueError:
                return err.code, raw.decode(errors="replace")

    def request_retry(self, method, path, body=None, ndjson=False, what="request"):
        """Retry on connection errors and 5xx until it works (or we are stopping)."""
        delay = 2
        while True:
            try:
                status, result = self.request(method, path, body, ndjson)
                if status < 500:
                    return status, result
                log(f"{what}: HTTP {status}, retrying in {delay}s")
            except OSError as err:
                log(f"{what}: {err}, retrying in {delay}s")
            if stopping:
                raise SystemExit(0)
            time.sleep(delay)
            delay = min(delay * 2, 30)


# ---------------------------------------------------------------- setup


def field_paths(value, prefix=""):
    """Leaf field paths of a document, e.g. {"a": {"b": 1}} -> {"a.b"}."""
    if isinstance(value, dict):
        paths = set()
        for key, child in value.items():
            paths |= field_paths(child, f"{prefix}{key}.")
        return paths
    return {prefix[:-1]}


def seed_fields(es, ds, seed, start):
    """Index a few real documents covering every field, right away.

    Without this, a data stream only gets its fields once its backfill starts, which
    can take minutes on a small machine. Kibana caches field lists in the browser, so a
    page opened in between keeps reporting "field not found".

    The documents are real ones (same ids as the backfill will produce), taken from the
    first hours of the range to load: nothing is added twice, and the resume point
    (latest document) must be computed before calling this.
    """
    end = min(start + SEED_FIELDS_WINDOW_SECONDS, int(time.time())) - 1
    known, writer, kept = set(), BulkWriter(es), 0
    for second in range(start, end + 1):
        for doc_id, doc in events_for_second(ds, second, seed):
            new = field_paths(doc) - known
            if new:
                known |= new
                writer.add(ds.data_stream, doc_id, doc)
                kept += 1
    writer.flush()
    log(f"[{ds.name}] {len(known)} fields mapped from {kept} sample documents")


def setup(es, kibana, datasets, seed, seed_from):
    """seed_from: {dataset name: first second to load} for empty data streams."""
    for ds in datasets:
        status, result = es.request_retry(
            "PUT", f"/_index_template/{ds.template_name}", ds.index_template(), what="index template"
        )
        if status != 200:
            sys.exit(f"Cannot install index template {ds.template_name}: {result}")
        log(f"[{ds.name}] index template {ds.template_name} installed")
        if seed_from.get(ds.name) is not None:
            seed_fields(es, ds, seed, seed_from[ds.name])

        if kibana is None:
            continue
        view = ds.data_view()
        status, _ = kibana.request_retry("GET", f"/api/data_views/data_view/{view['id']}", what="Kibana data view")
        if status == 404:
            status, result = kibana.request_retry(
                "POST", "/api/data_views/data_view", {"data_view": view}, what="Kibana data view"
            )
        if status == 200:
            log(f"[{ds.name}] Kibana data view \"{view['name']}\" ready")
        else:
            # Not fatal: data can still be explored by creating the data view by hand.
            log(f"[{ds.name}] WARNING: cannot create Kibana data view (HTTP {status})")


def time_bounds(es, ds):
    """(oldest, latest) epoch seconds of the documents, or (None, None) if there is no data yet."""
    body = {"size": 0, "aggs": {"oldest": {"min": {"field": "@timestamp"}},
                                "latest": {"max": {"field": "@timestamp"}}}}
    status, result = es.request_retry("POST", f"/{ds.data_stream}/_search", body, what="data time range")
    if status == 404:
        return None, None
    if status != 200:
        sys.exit(f"Cannot read the time range of {ds.data_stream}: {result}")
    oldest, latest = result["aggregations"]["oldest"]["value"], result["aggregations"]["latest"]["value"]
    if latest is None:
        return None, None
    return int(oldest / 1000), int(latest / 1000)


# ---------------------------------------------------------------- generation


def poisson(rng, lam):
    if lam <= 0:
        return 0
    if lam > 30:
        return max(0, int(round(rng.gauss(lam, math.sqrt(lam)))))
    limit, k, p = math.exp(-lam), 0, 1.0
    while True:
        p *= rng.random()
        if p <= limit:
            return k
        k += 1


def events_for_second(ds, second, seed):
    """Yield (doc_id, document) for one second. Deterministic."""
    rng = random.Random(f"{seed}:{ds.name}:{second}")
    count = poisson(rng, ds.rate(second))
    offsets = sorted(rng.random() for _ in range(count))
    docs = [ds.event(rng, second + offset) for offset in offsets]
    # Events at fixed times (e.g. periodic beacons), independent of the rate.
    docs.extend(ds.scheduled(rng, second))
    for i, doc in enumerate(docs):
        yield f"{ds.name}-{second}-{i}", doc


class BulkWriter:
    def __init__(self, es):
        self.es = es
        self.lines = []
        self.created = 0
        self.existing = 0
        self.failed = 0

    def add(self, index, doc_id, doc):
        self.lines.append(json.dumps({"create": {"_index": index, "_id": doc_id}}))
        self.lines.append(json.dumps(doc, separators=(",", ":")))
        if len(self.lines) >= BULK_SIZE * 2:
            self.flush()

    def flush(self):
        if not self.lines:
            return
        body = "\n".join(self.lines) + "\n"
        self.lines = []
        status, result = self.es.request_retry("POST", "/_bulk", body, ndjson=True, what="bulk")
        if status != 200:
            sys.exit(f"Bulk request rejected (HTTP {status}): {result}")
        errors_shown = 0
        for item in result["items"]:
            item_status = item["create"]["status"]
            if item_status == 201:
                self.created += 1
            elif item_status == 409:
                self.existing += 1
            else:
                self.failed += 1
                if errors_shown < 3:
                    log(f"document rejected: {item['create'].get('error')}")
                    errors_shown += 1


def generate_range(writer, datasets, start, end, seed):
    """Generate seconds [start, end] (inclusive). Returns False if interrupted."""
    for second in range(start, end + 1):
        if stopping:
            return False
        for ds in datasets:
            for doc_id, doc in events_for_second(ds, second, seed):
                writer.add(ds.data_stream, doc_id, doc)
    return True


def load(writer, datasets, starts, floor, end, seed, progress):
    """Load [start, end] for every dataset, one chunk per dataset in turn, so all
    datasets progress together. Returns False if interrupted."""
    cursors = dict(starts)
    docs = {ds.name: 0 for ds in datasets}
    span = max(1, end + 1 - floor)

    def percent(name):
        return min(100, int((cursors[name] - floor) * 100 / span))

    def emit(kind, ds):
        if progress:
            print(f"{kind} {ds.name} {percent(ds.name)} {docs[ds.name]}", flush=True)

    if progress:
        print(f"plan {floor} {end}", flush=True)
        for ds in datasets:
            emit("dataset", ds)
    log(f"load {fmt(floor)} -> {fmt(end)} UTC: {', '.join(ds.name for ds in datasets)}")
    began = last_emit = time.monotonic()

    while any(cursors[ds.name] <= end for ds in datasets):
        for ds in datasets:
            start = cursors[ds.name]
            if start > end:
                continue
            chunk_end = min(end, start + LOAD_CHUNK_SECONDS - 1)
            for second in range(start, chunk_end + 1):
                if stopping:
                    writer.flush()
                    return False
                for doc_id, doc in events_for_second(ds, second, seed):
                    writer.add(ds.data_stream, doc_id, doc)
                    docs[ds.name] += 1
            cursors[ds.name] = chunk_end + 1
            if cursors[ds.name] > end:
                writer.flush()
                emit("done", ds)
        now = time.monotonic()
        if now - last_emit >= LOAD_PROGRESS_EVERY_SECONDS:
            last_emit = now
            for ds in datasets:
                if cursors[ds.name] <= end:
                    emit("progress", ds)
    writer.flush()
    log(
        f"load done in {time.monotonic() - began:.0f}s: {writer.created} new docs, "
        f"{writer.existing} already present, {writer.failed} rejected"
    )
    return True


def fmt(second):
    return datetime.fromtimestamp(second, timezone.utc).strftime("%Y-%m-%d %H:%M")


def backfill(writer, datasets, start, end, seed):
    """Load [start, end] in chunks, with progress logs. Returns False if interrupted."""
    if start > end:
        return True
    log(f"backfill {fmt(start)} -> {fmt(end)} UTC")
    began = last_report = time.monotonic()
    created_before, existing_before, failed_before = writer.created, writer.existing, writer.failed
    second = start
    while second <= end:
        chunk_end = min(end, second + BACKFILL_CHUNK_SECONDS - 1)
        if not generate_range(writer, datasets, second, chunk_end, seed):
            writer.flush()
            return False
        second = chunk_end + 1
        now = time.monotonic()
        if now - last_report >= PROGRESS_EVERY_SECONDS:
            last_report = now
            done = (second - start) / (end - start + 1) * 100
            speed = (writer.created - created_before) / (now - began)
            log(f"backfill {done:5.1f}% (up to {fmt(second)}), {writer.created - created_before} new docs, {speed:.0f} docs/s")
    writer.flush()
    log(
        f"backfill done in {time.monotonic() - began:.0f}s: {writer.created - created_before} new docs, "
        f"{writer.existing - existing_before} already present, {writer.failed - failed_before} rejected"
    )
    return True


def follow(writer, datasets, start, seed):
    """Generate each second as soon as it is over, forever."""
    log("live mode: generating data in real time")
    second = start
    last_report = time.monotonic()
    created_before = writer.created
    while not stopping:
        last_complete = int(time.time()) - 1
        if second > last_complete:
            time.sleep(0.2)
            continue
        generate_range(writer, datasets, second, last_complete, seed)
        writer.flush()
        second = last_complete + 1
        now = time.monotonic()
        if now - last_report >= LIVE_REPORT_EVERY_SECONDS:
            log(f"live: {writer.created - created_before} new docs in the last {now - last_report:.0f}s")
            last_report = now
            created_before = writer.created
    writer.flush()


# ---------------------------------------------------------------- CLI


def parse_time(value):
    """ISO date/datetime, "now", or "now[+-]N[smhd]". Returns epoch seconds."""
    match = re.fullmatch(r"now(?:([+-])(\d+)([smhd]))?", value.strip())
    if match:
        now = int(time.time())
        if not match.group(1):
            return now
        delta = int(match.group(2)) * {"s": 1, "m": 60, "h": 3600, "d": 86400}[match.group(3)]
        return now + delta if match.group(1) == "+" else now - delta
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return int(parsed.timestamp())


def env(name, default=None):
    value = os.environ.get(name, "")
    return value if value != "" else default


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    rn = sub.add_parser("run")
    rn.add_argument("--existing-only", action="store_true",
                    help="only follow datasets that already have data (never load a new one)")
    sub.add_parser("setup")
    ld = sub.add_parser("load")
    ld.add_argument("--progress", action="store_true")
    bf = sub.add_parser("backfill")
    bf.add_argument("start")
    bf.add_argument("end")
    args = parser.parse_args()

    names = [n.strip() for n in env("DATAGEN_DATASETS", "apache,firewall,metrics,postgresql").split(",") if n.strip()]
    unknown = [n for n in names if n not in DATASETS]
    if unknown:
        sys.exit(f"Unknown dataset(s): {', '.join(unknown)}. Available: {', '.join(DATASETS)}")

    seed = env("DATAGEN_SEED", "easy-elk-lab")
    rate = float(env("DATAGEN_RATE", "0.1"))
    history_days = float(env("DATAGEN_HISTORY_DAYS", "7"))
    utc_offset = float(env("DATAGEN_UTC_OFFSET", "1"))
    datasets = [DATASETS[n](seed=seed, utc_offset_hours=utc_offset, rate=rate) for n in names]

    es = Http(env("ES_URL", "http://localhost:9200"), env("ES_USER", "elastic"), env("ES_PASSWORD", ""))
    kibana_url = env("KIBANA_URL")
    kibana = None
    if kibana_url:
        kibana = Http(kibana_url, env("ES_USER", "elastic"), env("ES_PASSWORD", ""), {"kbn-xsrf": "true"})

    signal.signal(signal.SIGTERM, on_signal)
    signal.signal(signal.SIGINT, on_signal)

    log(f"datasets: {', '.join(names)} | rate: {rate}/s per dataset (+ incidents) | seed: {seed}")
    # Latest document of each dataset, read before setup indexes any sample document.
    now = int(time.time())
    bounds = {ds.name: time_bounds(es, ds) for ds in datasets}
    oldest = {name: value[0] for name, value in bounds.items()}
    latest = {name: value[1] for name, value in bounds.items()}
    if args.command == "run" and args.existing_only:
        datasets = [ds for ds in datasets if latest[ds.name] is not None]
        if not datasets:
            log("no test data loaded yet, nothing to follow (load it with: ./lab.sh data load)")
            return
    if args.command == "backfill":
        first = parse_time(args.start)
    elif args.command in ("run", "load"):
        first = now - int(history_days * 86400)
    else:
        first = now - SEED_FIELDS_WINDOW_SECONDS
    setup(es, kibana, datasets, seed, {name: first for name, value in latest.items() if value is None})
    if args.command == "setup":
        return

    writer = BulkWriter(es)

    if args.command == "backfill":
        backfill(writer, datasets, first, parse_time(args.end), seed)
        return

    floor = first

    if args.command == "load":
        # Resume each dataset from its latest document, never before the history window.
        # If the data starts after the history window (e.g. --days 30 after a 7-day
        # load), load from the window start: existing documents are just found again.
        starts = {}
        for ds in datasets:
            last, first_doc = latest[ds.name], oldest[ds.name]
            if last is None or first_doc > floor + LOAD_CHUNK_SECONDS:
                starts[ds.name] = floor
            else:
                starts[ds.name] = max(floor, last - RESUME_OVERLAP_SECONDS)
        ok = load(writer, datasets, starts, floor, now - 1, seed, args.progress)
        sys.exit(0 if ok else 130)

    # run: resume each dataset from its latest document, never before the history window.
    resume = []
    for ds in datasets:
        last = latest[ds.name]
        start = floor if last is None else max(floor, last - RESUME_OVERLAP_SECONDS)
        resume.append(start)
        log(f"[{ds.name}] " + ("no data yet" if last is None else f"latest document at {fmt(last)} UTC"))
        if not backfill(writer, [ds], start, now - 1, seed):
            return
    follow(writer, datasets, max(now, max(resume)), seed)
    log("stopped")


if __name__ == "__main__":
    main()
