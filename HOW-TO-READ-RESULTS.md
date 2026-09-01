# How to read results

Guide to the JSON (and related) files analysis notebooks write next to your capture — what each file contains and how report fields map to activity in the log.

---

## Notebooks vs JSON sidecars

Two roles — use both:

| | **Notebooks** | **`{stem}-report.json`** (and `*-norm.json` where applicable) |
|---|---|---|
| **Purpose** | **Find the cause** — guided narrative from the log toward what stalled, what the API was doing, and who owns it (terraform / provider / sdk) | **Compare the same action** — stable, small JSON you can diff or query when the workload is the same but the provider, Terraform, or environment changed |
| **How** | Run cells in order; read Summary, stall/timing sections, SDK activity, issue attribution | Export report from the **same notebook** on each capture; compare fields (`summary`, `issue_attribution`, `sdk_timeline_summary`, `sdk_call_rates`, workflow tables) |
| **When** | First pass on any capture | After each capture is analyzed the same way — plan vs plan, hang vs hang, same workflow folder |

**Notebooks** answer “what happened and where should I look?” **`report.json`** answers “what changed between these two runs of the same thing?” without re-parsing multi-GB logs.

Normalized **`*-norm.json`** caches are for notebook speed; **`*-report.json`** is the deliberate export for sharing and side-by-side review.

---

## Why we create JSON sidecars

Raw captures (`TF_LOG=json` traces, `terraform plan -json` stdout) are often **very large** (hundreds of MB to multiple GB). Re-parsing them on every notebook run is slow.

We therefore write **sidecar files next to `TERRAFORM_LOG_PATH`**:

| Kind | Filename pattern | Created when | Why |
|---|---|---|---|
| **Normalized cache** | `{stem}-norm.json` | Automatically on first notebook run (unless cache disabled) | Stores **parsed, notebook-specific events** so reruns load in seconds instead of re-scanning the whole capture. Each workflow uses a different normalizer — the same raw log produces different `*-norm.json` content depending on which notebook you ran. |
| **SDK normalized cache** | `{stem}-sdk-norm.json` | Automatically by `sdk-plan/sdk-analysis.ipynb` | Parsed **SDK DEBUG request/response pairs** for the optional SDK deep-dive notebook only. |
| **Run report** | `{stem}-report.json` | When you run **Export report** in a hang or performance TF_LOG notebook | Structured summary of one capture — verdicts, timings, SDK rates, issue attribution. Export from the same notebook on each capture when comparing the same workload across provider or Terraform versions. |
| **Parser error log** | `{stem}-parse-*.log`, `{stem}-classify-tf-log-errors.log` | When a line fails JSON parse | Malformed lines only; confirms whether missing data is parse loss vs absent activity. |

**Normalized cache** = faster iteration in Jupyter.

**Report JSON** = one capture per file; compare reports from the same notebook when reviewing the same action across versions.

Set `DISABLE_NORMALIZED_CACHE=1` to skip writing caches. Set `FORCE_RENORMALIZE=1` to rebuild a stale cache after parser code changes.

---

## Hang vs performance notebooks

Both export the **same** `*-report.json` schema (so you can compare like with like). The **notebook UI** walks you toward the cause:

| Question | **Hang** (`*/hang-analysis.ipynb`) | **Performance** (`*/performance-analysis.ipynb`) |
|---|---|---|
| Where did it stop? | ✅ open resources, verdicts, tail | ❌ |
| Why is the graph stuck? | ✅ graph wait, imbalance, churn | ❌ |
| How long did finished work take? | only pairs completed before the stall | ✅ duration charts, slowest resources |
| When did API traffic spike? | ✅ **SDK pressure** (timeline + detail if elevated) | ✅ **Provider API activity** (timeline + key averages; 429 if present) |
| Who owns the issue (terraform / provider / sdk)? | ✅ **issue attribution** in Summary + report | ✅ **issue attribution** with SDK block + report |
| Raw HTTP / endpoint deep dive | → `sdk-plan/sdk-analysis.ipynb` | → `sdk-plan/sdk-analysis.ipynb` |

**Hang** leads with stall diagnosis; **one SDK pressure section** replaces separate retry/404/429/rate/timeline cells. **Performance** leads with timing; SDK is a short activity block — full retry/404 tables are in the exported report only.

Both notebook types print and export **`issue_attribution`** — a terraform / provider / sdk routing block derived from patterns in the log.

---

## Issue ownership: Terraform vs provider vs SDK

Every `*-report.json` from hang or performance TF_LOG notebooks includes **`issue_attribution`**. Use it to route tickets based on what the log shows.

| Layer | What it means | Typical signals in the report |
|---|---|---|
| **`terraform`** | Terraform core / graph — dependency expansion, refresh scheduling, spinning while waiting on provider RPCs | `graph_wait_loops`, `vertex_churn`, `summary.dag_wait_pairs`, verdict `graph_wait_loop` |
| **`provider`** | genesyscloud provider — which APIs run on Read/Create/Update/Export, schema-driven refresh timing, export-on-read | `stuck_refresh`, `stuck_export`, `stuck_apply`, `stuck_apply_rpc`, `sdk_timeline_summary`, `sdk_404` on export GETs, `sdk_call_rates_by_resource_type` |
| **`sdk`** | Genesys Cloud SDK / HTTP client — retries, rate-limit backoff (observed via SDK DEBUG hooks) | `sdk_retries`, `sdk_429_wait`, verdict `sdk_retry_storm`, `summary.sdk_429_wait_seconds` |

**Important:** SDK DEBUG sections measure *HTTP activity*. High volume or 404 polling usually means **provider logic chose to call the API**; retries and 429 sleep are **SDK/client behavior**. The report assigns layers accordingly — e.g. `sdk_not_found` verdicts map to **provider** (export-not-ready polling), not “missing object in Genesys Cloud.”

### Reading `issue_attribution`

| Field | Meaning |
|---|---|
| `primary_layer` | Highest-scoring layer (`terraform`, `provider`, or `sdk`) — **start here for ticket routing** |
| `layer_scores` | `{terraform, provider, sdk}` numeric scores (sum of signal scores in this capture) |
| `layers.<layer>.signals` | Ranked `{category, layer, summary, detail, score, report_sections}` |
| `layers.<layer>.ticket_routing` | One-line description of who to engage |
| `guidance` | Short “start with X, correlate with Y” sentence |

Also mirrored on **`summary.primary_layer`**:

```bash
jq '.summary.primary_layer, .issue_attribution.layers.provider.signals[0]' capture-report.json
```

### Report section → layer map

| Report section | Layer |
|---|---|
| `graph_wait_loops`, `vertex_churn`, `summary.dag_wait_pairs` | terraform |
| `open_refreshes`, `refresh_imbalance`, `open_exports`, `export_imbalance`, `open_applies`, `apply_imbalance`, `open_apply_rpcs` | provider (stall) |
| `sdk_timeline`, `sdk_timeline_summary`, `sdk_404`, `sdk_call_rates`, `sdk_call_rates_by_resource_type` | provider (API volume / polling) — unless only 429/retry |
| `sdk_retries`, `sdk_429_wait`, `sdk_429_wait_by_resource_type`, `summary.sdk_429_wait_*` | sdk |
| `ranked_verdicts` / `summary.verdicts` | each row includes **`layer`** |

---

## Per notebook

Paths below are under `notebooks/` unless noted.

### `whatisit.ipynb`

| | |
|---|---|
| **Capture** | Any file at `TERRAFORM_LOG_PATH` |
| **Creates** | `{stem}-classify-tf-log-errors.log` only if JSON lines fail to parse |
| **Does not create** | `*-norm.json`, `*-report.json` |

**Why:** Routes you to the right analysis notebook. It classifies the capture in memory; it is not an analysis or export workflow.

---

### `sdk-plan/plan-analysis.ipynb`

| | |
|---|---|
| **Capture** | `terraform plan -json` UI stdout (e.g. `plan-ui.json`) |
| **Creates automatically** | `{stem}-norm.json` — normalized `refresh_start`, `refresh_complete`, `planned_change`, `resource_drift`, etc. |
| **Creates on demand** | — (no report export today) |
| **Parser errors** | `{stem}-parse-plan-output-errors.log` |

**Why `*-norm.json`:** Plan UI files can still be large; the cache holds structured refresh/drift/planned-change events for charts and tables without re-parsing NDJSON every run.

**What to read in `*-norm.json`:** Array of normalized UI events with timing fields the notebook uses for refresh duration and drift views. **No SDK DEBUG data** — UI JSON does not contain provider HTTP hooks.

---

### `sdk-plan/sdk-analysis.ipynb`

| | |
|---|---|
| **Capture** | `TF_LOG=json` (export, plan, or apply) with SDK DEBUG hooks in the log |
| **Creates automatically** | `{stem}-sdk-norm.json` — paired SDK request/response records (sanitized URLs) |
| **Creates on demand** | — (no report export) |
| **Parser errors** | `{stem}-parse-sdk-errors.log` |

**Why `*-sdk-norm.json`:** Extracting and pairing SDK lines from a multi-GB TF_LOG file is expensive; this cache is separate from workflow `*-norm.json` because the SDK notebook uses a different shape.

**What to read in `*-sdk-norm.json`:** Raw paired SDK calls for interactive exploration in the notebook (counts, charts, optional request bodies if `sdk_debug` was enabled at capture time).

---

### `plan/performance-analysis.ipynb`

| | |
|---|---|
| **Capture** | Completed **`TF_LOG=json`** plan trace |
| **Creates automatically** | `{stem}-norm.json` — normalized refresh/trace events from TF_LOG |
| **Creates on demand** | `{stem}-report.json` via **Export report** |
| **Parser errors** | `{stem}-parse-plan-log-errors.log` |

**Why `*-norm.json`:** Same performance reason; normalizer is TF_LOG plan trace (not UI JSON). **`resource_drift` is not in this format.**

**Why `*-report.json`:** Summarizes a **finished** plan — slow refreshes plus SDK behavior over the full log span.

**Report sections to read:**

| Section | Meaning |
|---|---|
| `summary` | Run duration, SDK totals, top-level counts — see [Shared report fields](#shared-report-fields) |
| `completed_refreshes` | Refreshes that finished; slow resources and timings |
| `sdk_retries`, `sdk_404`, `sdk_call_rates`, `sdk_call_rates_by_resource_type`, `sdk_timeline`, `sdk_timeline_summary` | SDK DEBUG activity in the log |

**In the notebook:** **Type / duration / longest refresh** sections, then **Provider API activity** (timeline + key endpoints), then Export report.

---

### `plan/hang-analysis.ipynb`

| | |
|---|---|
| **Capture** | Hung, killed, or partial **`TF_LOG=json`** plan trace |
| **Creates automatically** | — (scans raw log in memory; no `*-norm.json`) |
| **Creates on demand** | `{stem}-report.json` via **Export report** |
| **Parser errors** | `{stem}-parse-hang-errors.log` |

**Why no norm cache:** Hang scanning walks the full log once for stall patterns, open refreshes, graph waits, and SDK counters in a single pass tuned for diagnosis.

**Why `*-report.json`:** Captures **where the run stopped** and **what the provider was doing** (SDK rates, retries, 404s) in a shareable file — useful when the capture ends before the run finished (interrupt, OOM, etc.).

**Report sections to read:**

| Section | Meaning |
|---|---|
| `summary` | Duration of captured window, `primary_summary`, verdicts — **start here** |
| `open_refreshes` | Refreshes still open at last log line — **where it died** |
| `completed_refreshes` | Refreshes that finished before the stall |
| `ranked_verdicts` | Hang patterns scored (graph wait, SDK storm, etc.) |
| `graph_wait_loops` | Repeated `dag/walk` waits on the same target |
| `refresh_imbalance`, `vertex_churn` | Refresh/graph thrashing |
| `tail_messages` | Last log lines before capture ended |
| SDK sections | Same as completed plan — API activity during the captured window |

**In the notebook:** Stall sections (open refresh, graph wait, imbalance, …), then **SDK pressure** (timeline + elevated retry/404/429), then tail + Export report.

**Hang captures:** Use `requests_per_minute` in `sdk_call_rates` (not raw counts alone) because `summary.duration_minutes` may reflect a partial run. Pair with `sdk_timeline_summary` for when export polling started.

---

### `export/performance-analysis.ipynb`

| | |
|---|---|
| **Capture** | Completed **`TF_LOG=json`** resource export |
| **Creates automatically** | `{stem}-norm.json` — export start/complete events |
| **Creates on demand** | `{stem}-report.json` via **Export report** |
| **Parser errors** | `{stem}-parse-export-errors.log` |

**Why `*-report.json`:** Summarizes completed export timing plus SDK API volume from the log.

**Report sections to read:** `summary`, `completed_exports`, SDK sections (same names as plan).

**In the notebook:** Export timing charts, then **Provider API activity**, then Export report.

---

### `export/hang-analysis.ipynb`

| | |
|---|---|
| **Capture** | Hung or partial **`TF_LOG=json`** export |
| **Creates automatically** | — |
| **Creates on demand** | `{stem}-report.json` via **Export report** |
| **Parser errors** | `{stem}-parse-hang-errors.log` |

**Report sections to read:** `summary`, `open_exports`, `completed_exports`, `ranked_verdicts`, `export_imbalance`, `tail_export_messages`, SDK sections, `tail_messages`.

**In the notebook:** Export timing, stuck exports, **SDK pressure**, tail sections, Export report.

---

### `apply/performance-analysis.ipynb`

| | |
|---|---|
| **Capture** | Completed apply — **`TF_LOG=json`** or non-interactive **`terraform apply -json`** UI (see notebook intro) |
| **Creates automatically** | `{stem}-norm.json` (shape depends on capture format) |
| **Creates on demand** | `{stem}-report.json` via **Export report** |
| **Parser errors** | `{stem}-parse-apply-errors.log` |

**Report sections to read:** `summary`, `completed_applies`, SDK sections.

**In the notebook:** Apply timing charts, then **Provider API activity**, then Export report.

---

### `apply/hang-analysis.ipynb`

| | |
|---|---|
| **Capture** | Hung or partial apply **`TF_LOG=json`** |
| **Creates automatically** | — |
| **Creates on demand** | `{stem}-report.json` via **Export report** |
| **Parser errors** | `{stem}-parse-hang-errors.log` |

**Report sections to read:** `summary`, `open_applies`, `completed_applies`, `ranked_verdicts`, `apply_imbalance`, `open_apply_rpcs`, `graph_wait_loops`, SDK sections, `tail_messages`.

**In the notebook:** Apply timing, stuck/open RPC/graph sections, **SDK pressure**, tail, Export report.

---

## Shared report fields

All `*-report.json` files share a common envelope and SDK blocks. Hang vs performance notebooks add workflow-specific sections (tables above).

### Envelope

| Field | Meaning |
|---|---|
| `source_log` | Capture path analyzed |
| `workflow` | `plan`, `export`, or `apply` |
| `generated_at` | Report export time (UTC) |

Use reports produced by the **same notebook** as the capture you analyzed.

### `summary`

| Field | What it tells you |
|---|---|
| `duration_minutes` | First → last log timestamp in the capture |
| `parsed_lines` | Successfully parsed lines |
| `dag_wait_pairs` | Count of graph wait events (`dag/walk: vertex … is waiting for …`) |
| `sdk_request_total` / `sdk_response_total` | Total SDK DEBUG hook volume |
| `sdk_retry_endpoints`, `sdk_404_endpoints`, `sdk_429_endpoints` | Distinct endpoints with retries, 404s, or 429s |
| `sdk_429_wait_seconds`, `sdk_429_wait_minutes`, `sdk_429_wait_pct_of_log` | Summed `invocation_retry_after` on 429 responses |
| `sdk_status_codes` | HTTP status histogram from SDK DEBUG responses |
| `primary_category`, `primary_summary`, `primary_detail` | Top hang verdict — **read first on partial captures** |
| `primary_layer` | Top issue owner (`terraform`, `provider`, `sdk`) — same as `issue_attribution.primary_layer` |
| `verdicts` | Ranked `{category, layer, summary, detail, score}` — see [Verdict categories](#verdict-categories) |

### `issue_attribution`

Structured **terraform / provider / sdk** routing from patterns in this capture. See [Issue ownership](#issue-ownership-terraform-vs-provider-vs-sdk).

| Field | Meaning |
|---|---|
| `primary_layer` | Highest-scoring layer in this capture |
| `layer_scores` | Per-layer score totals (run-specific) |
| `layers.*.signals` | Ranked signals; each lists `report_sections` to read next |

### Verdict categories

| `category` | Layer | Usually means |
|---|---|---|
| `graph_wait_loop` | terraform | Blocked on the same graph node (often module var expand) |
| `vertex_churn` | terraform | Graph vertex thrashing |
| `stuck_refresh` | provider | Provider Read has not returned; Terraform still waiting |
| `stuck_export` / `stuck_apply` | provider | Resource operation started, never finished |
| `stuck_apply_rpc` | provider | Apply RPC still open at log end |
| `sdk_retry_storm` | sdk | Many retry-after responses on one endpoint |
| `sdk_not_found` | provider | Repeated 404s from provider read/polling (often export-not-ready) |

### SDK sections (all TF_LOG report exports)

**`sdk_retries`** — endpoints with SDK backoff (`invocation_retry_after`).

**`sdk_404`** — endpoints with repeated 404s (often DNC export polling before file is ready).

**`sdk_429_wait`** — per-endpoint **429** counts and summed **`invocation_retry_after`** (seconds/minutes the SDK was told to wait on rate limits). Check **`sdk_429_wait_pct_of_log`** in `summary` for share of log span spent waiting.

**`sdk_429_wait_by_resource_type`** — same wait rollup by Terraform resource type.

**`sdk_call_rates`** — per **API endpoint** (`method_url`), rates normalized by `summary.duration_minutes`:

| Column | Meaning |
|---|---|
| `requests_per_minute` / `responses_per_minute` | Average rate over the captured log span |
| `response_404_per_minute`, `response_429_per_minute` | Error rates over the log span |
| `is_dnclist_export` | `true` for `GET …/dnclists/…/export` polling |

**`sdk_call_rates_by_resource_type`** — same idea by Terraform `resource_type` (e.g. `genesyscloud_outbound_dnclist`).

**`sdk_timeline`** — SDK **request counts per minute from log start** (1-minute buckets), long format: `minute_from_start`, `method_url`, `request_count`, `response_404`, `is_dnclist_export`. Top endpoints plus all DNC export URLs are included (capped at 500 rows in the report).

**`sdk_timeline_summary`** — peak-minute stats for DNC list export polling:

| Field | Meaning |
|---|---|
| `first_dnclist_export_get_minute` | When export GET polling first appears in the log |
| `dnclist_export_get_peak.peak_requests` | Busiest minute for export GET |
| `dnclist_export_get_peak.minute_from_start` | Which minute that peak occurred |
| `dnclist_export_post_peak` | Same for export POST (initiate export) |

**Correlating fields in one capture:** If `graph_wait_loops` show Terraform waiting on a module variable while `sdk_timeline_summary.first_dnclist_export_get_minute` is early and `sdk_404` is elevated on export GETs, the log shows **provider** export-on-read polling overlapping **terraform** graph expansion — see `issue_attribution.layer_scores` for scored layers.

---

## Reading a report

**On one capture (cause):**

1. Run **`whatisit.ipynb`** if the capture type is unclear.
2. Open the matching analysis notebook (hang vs performance, same workflow folder).
3. Read Summary → stall or timing sections → SDK activity → **issue attribution** (`primary_layer`).

**Across captures (same action, different version):**

1. Analyze each capture with the **same notebook** (both hang, or both performance — same `plan` / `export` / `apply` folder).
2. **Export report** on each → two `*-report.json` files.
3. Compare stable fields: `summary.primary_layer`, `issue_attribution.layer_scores`, `sdk_timeline_summary`, `sdk_call_rates` (use per-minute columns on partial captures).

Example queries on one file:

```bash
jq '.summary.primary_layer' capture-report.json
jq '.sdk_call_rates[] | select(.is_dnclist_export)' capture-report.json
jq '.issue_attribution.layers' capture-report.json
```

---

## Related tools

| Tool | Role |
|---|---|
| **`log-chomper/`** | CLI response-time percentiles by endpoint (complements SDK sections in reports) |
| **`README.md`** | Capture setup, debug levels, notebook matrix |
