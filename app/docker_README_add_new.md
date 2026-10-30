# Add New Docker Container

A Dockerized service that fetches property URLs, scrapes new listings, inserts/updates them in MongoDB, and logs progress/errors. The service runs in cycles and retries transient failures automatically.

---

## Features

* **Configurable concurrency** via `MAX_CONCURRENCY` (env; default **10**)
* **Automatic retry** for URL discovery, batch step 1 & step 2, and error-log sending
* **Per-batch progress logs** (step 1 & step 2) under `data/`
* **Error logs** per batch for step 2 (`fail_address_step2_batch_<n>.csv`)

---

## Prerequisites

* Docker ≥ 20.10
* A local `data/` directory (mounted or created in-container)
* Reachable MongoDB and credentials configured via your app’s config/env (e.g., `app/db/session.py`)

---

## Build

From the project root:

```bash
docker build -t add_new -f Dockerfile_add_new .
```

This installs runtime deps, copies your app code, and sets `run.sh` as the entrypoint.

---

## Run

Basic run (concurrency = 20):

```bash
docker run --rm \
  -e MAX_CONCURRENCY=20 \
  add_new
```

Persist outputs on the host:

```bash
docker run --rm \
  -v "$(pwd)/data:/app/data" \
  -e MAX_CONCURRENCY=20 \
  add_new
```

> The service runs in a loop: discover URLs → process batches (step 1 → step 2) → send error logs → sleep → repeat.

---

## Environment Variables

| Name              | Default              | Purpose                                                           |
| ----------------- | -------------------- | ----------------------------------------------------------------- |
| `MAX_CONCURRENCY` | `10`                 | Worker threads / async slots used by Python scripts.              |
| `RETRY_DELAY`     | `5`                  | Seconds to wait between retries when a step fails.                |
| `CYCLE_DELAY`     | `60`                 | Seconds to wait between full cycles.                              |
| `BATCH_COUNT`     | `16`                 | If `BATCH_START/END` not set, process batches `0..BATCH_COUNT-1`. |
| `BATCH_START`     | `0`                  | First batch index to process each cycle.                          |
| `BATCH_END`       | `BATCH_COUNT-1`      | Last batch index to process each cycle.                           |
| `TZ`              | `Australia/Brisbane` | Timezone for timestamps in logs.                                  |

> Python scripts read `MAX_CONCURRENCY` from the environment (not CLI). The bash runner prints it for visibility.

---

## What Happens Inside

1. **`run.sh`** (entrypoint)

   * Cleans stale artifacts (`urls_sale.json`, `log_progress*.txt`, `fail_address*.csv`, `batch*`)
   * Runs **`docker_get_urls.py`** in a retry loop until it writes `data/urls_sale.json`
   * For each batch from `BATCH_START` to `BATCH_END`:

     * Runs **`docker_step1.py`** (scrape per suburb) — retried until success
     * Runs **`docker_add_new.py`** (insert/update “new sale/sold”, images, agent links, etc.) — retried until success
   * Runs **`docker_send_error_log.py`** (also retried)
   * Sleeps `CYCLE_DELAY` seconds and repeats

2. **`docker_get_urls.py`**

   * Reads MongoDB, collects listing URLs, writes `data/urls_sale.json`

3. **`docker_step1.py`**

   * Uses `MAX_CONCURRENCY` from env
   * Scrapes per-suburb lists into `data/batch_<n>/*.csv`
   * Logs to `data/log_progress_batch_<n>.txt`

4. **`docker_add_new.py`**

   * Uses `MAX_CONCURRENCY` from env
   * Processes URLs (new sales & sold updates), writes per-batch logs:

     * `data/log_progress_step2_batch_<n>.txt`
     * errors to `data/fail_address_step2_batch_<n>.csv`

---

## Outputs

* `data/urls_sale.json` — discovered listing URLs
* `data/batch_<n>/*.csv` — step 1 outputs per suburb
* `data/log_progress_batch_<n>.txt` — step 1 progress
* `data/log_progress_step2_batch_<n>.txt` — step 2 progress
* `data/fail_address_step2_batch_<n>.csv` — step 2 error rows

---

## Debugging / One-off Runs

Run specific steps manually inside the container (or on your host):

```bash
# Step 1 for a single batch
python docker_step1.py --batch-order 3

# Step 2 for a single batch
python docker_add_new.py --batch-order 3
```

> Both scripts read `MAX_CONCURRENCY` from env (default 10). Example:
>
> ```bash
> MAX_CONCURRENCY=6 python docker_add_new.py --batch-order 3
> ```

For DNS / MongoDB issues (e.g., SRV lookups timing out), ensure the container can resolve your MongoDB hostnames and reach the network (VPC/VPN, DNS config, etc.).

---

## License

MIT — see [LICENSE](./LICENSE).
