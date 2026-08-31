# Introduction
As the support team for CX as Code, we are sometimes pulled in to examine a customer's environment or one of our own internal environments to understand what is happening in their Terraform/CX as Code environment.

As a result of these requests, we have written several Python and Jupyter Notebook scripts to process and analyze the data.  We are releasing these into our lab environment "AS-IS" with no guarantee of support.  Our goal is to share these scripts so that the large CX as Code community can use them as they see fit.

# Installation

All requirements were captured in a Python `requirements.txt` file.  The libraries can be
installed using `pip install -r requirements.txt`.

**One-time setup after clone** — register the git filter that strips notebook outputs on commit (uses `.gitattributes` in this repo):

```bash
nbstripout --install --attributes .gitattributes
nbstripout --status   # should show *.ipynb: filter: nbstripout
```

You do not need to clear outputs manually before each commit; git removes them when staging `.ipynb` files. Re-run the install command if you switch virtualenvs (the filter points at the Python that ran `nbstripout --install`).

Start Jupyter from the **`notebooks/`** directory (or open notebooks from its workflow subfolders). Each notebook runs a small bootstrap so `commonlib/` imports resolve correctly.

```
notebooks/
  notebook_setup.py   # import bootstrap for workflow subfolders
  whatisit.ipynb      # classify a capture and pick the right notebook
  sdk-analysis.ipynb  # SDK DEBUG call counts from TF_LOG captures
  commonlib/          # shared Python helpers
  export/             # completed + hang export analysis
  plan/               # UI output, TF_LOG trace, and hang plan analysis
  apply/              # completed + hang apply analysis
```

# Configuration
Notebooks read paths from the `commonlib/config.Config` class:

```
export TERRAFORM_LOG_PATH=""        # Input: raw capture file to analyze (required)
export DISABLE_NORMALIZED_CACHE=""  # Optional: set to 1 to skip cache read/write (in-memory only)
export FORCE_RENORMALIZE=""         # Optional: set to 1 to ignore existing cache and rebuild
```

**Set variables in the shell before starting Jupyter** — the notebook kernel inherits your terminal environment:

```bash
export TERRAFORM_LOG_PATH=/path/to/plan-tflog.log
cd notebooks
jupyter lab
```

## Normalized cache (automatic)

All analysis notebooks (including hang notebooks) use a **read-through cache** by default. Cache files are written next to the capture file — no extra env vars required.

| Raw capture | Auto cache (terraform normalizers) | Auto cache (SDK notebook) |
|---|---|---|
| `plan-tflog.log` | `plan-tflog-norm.json` | `plan-tflog-sdk-norm.json` |
| `plan-ui.json` | `plan-ui-norm.json` | — |
| `export-tflog.log` | `export-tflog-norm.json` | `export-tflog-sdk-norm.json` |
| `apply-tflog.log` | `apply-tflog-norm.json` | `apply-tflog-sdk-norm.json` |

When a cache file exists and is **at least as new as** the raw capture, notebooks load it instead of re-parsing the log. The first run (or any run after the capture changes) writes the cache and prints `Wrote normalized cache: ...`. Later runs print `Using normalized cache: ...`.

Each notebook workflow uses a **different normalizer**, so the same capture produces different `*-norm.json` content depending on which notebook you run (plan UI vs plan TF_LOG vs export, etc.). SDK analysis uses a separate `*-sdk-norm.json` cache.

**Escape hatches:**

| Variable | When to use |
|---|---|
| `DISABLE_NORMALIZED_CACHE=1` | Read-only capture directory, no sidecar files next to customer logs, or debugging without touching disk |
| `FORCE_RENORMALIZE=1` | Rebuild cache after parser code changes (still writes cache unless disabled) |

```bash
export DISABLE_NORMALIZED_CACHE=1   # in-memory only; no *-norm.json files
export FORCE_RENORMALIZE=1          # ignore stale cache, re-parse raw capture
```

All notebooks use `TERRAFORM_LOG_PATH`. Every analysis notebook except `whatisit.ipynb` normalizes the raw capture.

**Capture file naming** (examples only — any path works):

| Capture | Filename example |
|---|---|
| Plan UI (`terraform plan -json`) | `plan-ui.json` |
| Apply UI (`terraform apply -json`) | `apply-ui.json` |
| Export TF_LOG (completed) | `export-tflog.log` |
| Plan TF_LOG (completed) | `plan-tflog.log` |
| Apply TF_LOG (completed) | `apply-tflog.log` |
| Export TF_LOG (hung/partial) | `export-hang.log` |
| Plan TF_LOG (hung/partial) | `plan-hang.log` |
| Apply TF_LOG (hung/partial) | `apply-hang.log` |

Use `.json` for UI stdout captures and `.log` for `TF_LOG=json` trace files (NDJSON lines).

# Debugging

Two logging mechanisms apply for **level 2 and 3** captures. **Level 1** uses **`terraform plan -json` / `terraform apply -json`** stdout instead — no `TF_LOG` required. There is **no `TF_DEBUG` environment variable** — SDK activity in TF_LOG captures comes from the provider's HTTP hooks.

Enable what you need before running export, plan, or apply; then set **`TERRAFORM_LOG_PATH`** to the file you want a notebook to read.

## Terraform / provider logging (`TF_LOG`)

Standard Terraform environment variables. Captures Terraform core and provider messages, including **`SDK DEBUG REQUEST` / `SDK DEBUG RESPONSE`** hook lines emitted on every Genesys Cloud API call when logging is enabled.

**macOS / Linux**

```bash
export TF_LOG=json
export TF_LOG_PATH="./plan-tflog.log"
```

**Windows (PowerShell)**

```powershell
$env:TF_LOG="JSON"
$env:TF_LOG_PATH=".\plan-tflog.log"
```

**Disable (macOS / Linux)**

```bash
unset TF_LOG
unset TF_LOG_PATH
```

**Disable (Windows PowerShell)**

```powershell
Remove-Item Env:TF_LOG
Remove-Item Env:TF_LOG_PATH
```

## Genesys Cloud SDK/API logging (`sdk_debug`)

Optional provider setting for **richer** SDK visibility. The provider always writes compact SDK hook JSON to the Terraform log (when `TF_LOG` is on). Enabling **`sdk_debug`** adds full HTTP request bodies to those hook lines and also writes a separate **`sdk_debug.log`** file via the Genesys Cloud SDK logger.

**Provider block**

```hcl
sdk_debug        = true
sdk_debug_format = "Json"
```

**Environment variables** (equivalent to the provider attributes above)

```bash
export GENESYSCLOUD_SDK_DEBUG=true
export GENESYSCLOUD_SDK_DEBUG_FORMAT=Json
# optional: export GENESYSCLOUD_SDK_DEBUG_FILE_PATH=./sdk_debug.log
```

To disable, set `sdk_debug = false` (or `GENESYSCLOUD_SDK_DEBUG=false`) and remove or comment out `sdk_debug_format`.

These notebooks parse **`SDK DEBUG`** hook lines embedded in the **`TF_LOG`** capture. They do **not** support standalone **`sdk_debug.log`** files (different JSON shape from the SDK platform logger).

## Choosing the right debug level

| Debug level | Purpose |
|-------------|---------|
| `TF_LOG` | Terraform execution, provider behavior, resource processing, and SDK hook summaries (`SDK DEBUG REQUEST` / `RESPONSE`) |
| `sdk_debug` | Full request bodies in hook JSON plus a separate `sdk_debug.log` from the Genesys Cloud SDK |

For complex issues, enable **`TF_LOG`** and **`sdk_debug`** together.

# Pick a notebook

**Pick the workflow (export / plan / apply), then pick how the run went (finished normally → finished but need trace detail → hung or killed).** Set **`TERRAFORM_LOG_PATH`** to your capture file, run **`whatisit.ipynb`** if unsure what the file contains, then open the matching notebook.

| | **Level 1 — UI JSON (stdout)** | **Level 2 — TF_LOG (completed)** | **Level 3 — TF_LOG (hung)** |
|--|--|--|--|
| | *How long? Drift? Planned changes?* | *Slow or odd, but finished — need trace or export* | *Stuck? Retries? 404 storms? Graph spinning?* |
| **Export** | — *(no UI JSON capture)* | `export/analysis.ipynb` | `export/hang-analysis.ipynb` |
| **Plan** | `plan/output-analysis.ipynb` | `plan/log-analysis.ipynb` | `plan/hang-analysis.ipynb` |
| **Apply** | `apply/analysis.ipynb` *(non-interactive only)* | `apply/analysis.ipynb` | `apply/hang-analysis.ipynb` |

**SDK API call counts** (any workflow, level 2/3 captures): `sdk-analysis.ipynb` or **`log-chomper/`** for response-time percentiles.

# Three levels of analysis

Capture details for each cell in the matrix above. All levels use **JSON** — either Terraform **UI JSON** on stdout (`-json`) or **`TF_LOG=json`** diagnostic trace (different formats; do not mix them in one file).

**Export:** there is **no level 1** — resource export has no `terraform export -json` UI stream.

**`-json` is not `TF_LOG`:** level 1 lines look like `{"@module":"terraform.ui","type":"refresh_start",...}`. Level 2/3 lines look like `{"@level":"info","@message":"...",...}` (and may include `SDK DEBUG` hook JSON in `@message`).

## Level 1 captures (UI JSON stdout)

Redirect **stdout** to a file, then set **`TERRAFORM_LOG_PATH`**. No `TF_LOG` required.

**Plan** (interactive OK; includes drift)

```bash
terraform plan -json > plan-ui.json
export TERRAFORM_LOG_PATH=plan-ui.json
```

**Apply** (must be non-interactive — `-json` cannot prompt for approval)

```bash
# Option A: auto-approve
terraform apply -json -auto-approve > apply-ui.json

# Option B: saved plan (often preferred)
terraform plan -out=plan.tfplan
terraform apply -json plan.tfplan > apply-ui.json

export TERRAFORM_LOG_PATH=apply-ui.json
```

If you need an **interactive** apply with prompts, skip level 1 and use **level 2** (`TF_LOG=json`) instead.

## Level 2 captures (`TF_LOG=json`, completed run)

Point **`TERRAFORM_LOG_PATH`** at the log file before opening a notebook. Add **`sdk_debug`** when you need request bodies or the separate SDK log file.

**Export** (level 2 only — no UI JSON equivalent)

```bash
export TF_LOG=json
export TF_LOG_PATH=export-tflog.log
# optional: GENESYSCLOUD_SDK_DEBUG=true GENESYSCLOUD_SDK_DEBUG_FORMAT=Json
# run your Genesys Cloud resource export
export TERRAFORM_LOG_PATH=export-tflog.log
```

**Plan**

```bash
export TF_LOG=json
export TF_LOG_PATH=plan-tflog.log
# optional: GENESYSCLOUD_SDK_DEBUG=true GENESYSCLOUD_SDK_DEBUG_FORMAT=Json
terraform plan
export TERRAFORM_LOG_PATH=plan-tflog.log
```

**Apply** (interactive OK)

```bash
export TF_LOG=json
export TF_LOG_PATH=apply-tflog.log
# optional: GENESYSCLOUD_SDK_DEBUG=true GENESYSCLOUD_SDK_DEBUG_FORMAT=Json
terraform apply
export TERRAFORM_LOG_PATH=apply-tflog.log
```

## Level 3 captures (`TF_LOG=json`, hung or partial)

Use the same **`TF_LOG=json`** setup as level 2 while the run is stuck, or immediately after killing it. Point **`TF_LOG_PATH`** / **`TERRAFORM_LOG_PATH`** at a hang capture name (for example `plan-hang.log`, `apply-hang.log`, or `export-hang.log`), then open the matching `*/hang-analysis.ipynb` notebook.

# Workflow

1. Run **`whatisit.ipynb`** against `TERRAFORM_LOG_PATH` if you are not sure what the file contains.
2. Use the **Pick a notebook** matrix above (workflow × level), then open that notebook under `export/`, `plan/`, or `apply/`.

# Analysis notebooks

Notebooks are grouped by workflow under **`notebooks/`** (`export/`, `plan/`, `apply/`). Start with **`whatisit.ipynb`** at the notebooks root when routing; use **`sdk-analysis.ipynb`** for SDK call counts. Use the **Pick a notebook** matrix to choose one. Notes:

- **`plan/output-analysis.ipynb`** parses **`terraform plan -json`** UI records (`refresh_start`, `resource_drift`, `planned_change`, etc.).
- **`plan/log-analysis.ipynb`** parses **`TF_LOG=json`** trace logs captured during plan (as in `generator/generator.py` and `TF_LOG_PATH` workflows). **`resource_drift` is not available** in this format.
- **Hang notebooks** (`export/hang-analysis.ipynb`, `plan/hang-analysis.ipynb`, `apply/hang-analysis.ipynb`) use the same **`TF_LOG=json`** captures but focus on stall patterns rather than completed-run timing.
- **`sdk-analysis.ipynb`** parses `SDK DEBUG` request/response pairs embedded in **`TF_LOG`** output (emitted by provider HTTP hooks on every API call). Enable **`sdk_debug`** for full request bodies; it is not required for call counts or hang retry/404 analysis.

**Future work:** see [`TODO.md`](TODO.md) for planned enhancements (export filter delta, 429 wait timing).

# Shared library

Python helpers live in **`notebooks/commonlib/`** (`classify_tf_log.py`, `prep_hang_data.py`, `prep_*_data.py`, `gencharts.py`, `config.py`).

# Other tools

- **`log-chomper/`** — CLI for SDK request/response pairing and **response-time percentiles by endpoint** (complements `sdk-analysis.ipynb`, which focuses on call counts).
- **`generator/`** — small script to generate many resources so there is enough activity to parse and log.
