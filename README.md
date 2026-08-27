# Introduction
As the support team for CX as Code, we are sometimes pulled in to examine a customer's environment or one of our own internal environments to understand what is happening in their Terraform/CX as Code environment.

As a result of these requests, we have written several Python and Jupyter Notebook scripts to process and analyze the data.  We are releasing these into our lab environment "AS-IS" with no guarantee of support.  Our goal is to share these scripts so that the large CX as Code community can use them as they see fit.

# Installation

All requirements were captured in a Python `requirements.txt` file.  The libraries can be
installed using `pip install -r requirements.txt`.

Open notebooks from the `sdk-plan-export-notebooks` directory so Python imports resolve to `commonlib/`.

# Configuration
All paths used in these notebooks are read from the `commonlib/config.Config` class.  This class reads and writes based on three environment variables:

```
export TERRAFORM_LOG_PATH=""           # Location of the log file to analyze
export NORMALIZED_TERRAFORM_LOG_PATH="" # Optional output path for normalized Terraform log data
export NORMALIZED_GENESYS_SDK_PATH=""   # Optional output path for normalized SDK data
```

# Capturing logs

Most workflows use **`TF_LOG=json`** trace output written to a file via **`TF_LOG_PATH`**. SDK API lines require **`TF_DEBUG=JSON`** (or `TF_DEBUG=json`) in the same shell.

Typical export capture:

```bash
export TF_LOG=json
export TF_LOG_PATH=my-run-export_tf_json.log
export TF_DEBUG=JSON
# run your Genesys Cloud resource export
export TERRAFORM_LOG_PATH=my-run-export_tf_json.log
```

Typical plan capture:

```bash
export TF_LOG=json
export TF_LOG_PATH=my-run-plan_tf_json.log
export TF_DEBUG=JSON
terraform plan
export TERRAFORM_LOG_PATH=my-run-plan_tf_json.log
```

Typical apply capture:

```bash
export TF_LOG=json
export TF_LOG_PATH=my-run-apply_tf_json.log
export TF_DEBUG=JSON
terraform apply
export TERRAFORM_LOG_PATH=my-run-apply_tf_json.log
```

For **`terraform plan -json`** machine-readable UI output (original upstream format):

```bash
terraform plan -json > plan-ui.json
export TERRAFORM_LOG_PATH=plan-ui.json
```

# Workflow

1. Run **`whatisit.ipynb`** against `TERRAFORM_LOG_PATH` to classify the file (export / plan / apply) and see which notebook to open next.
2. For **completed** runs, open the matching `*-analysis.ipynb` notebook (timing and breakdown).
3. For **hung or very slow** runs, open the matching `*-hang-analysis.ipynb` notebook instead.

# Common questions

If you are trying to answer:

- **What kind of log file is this, and which notebook should I open?** → `whatisit.ipynb`
- **How long did a completed export or apply take, and which resources were slowest?** → `export-analysis.ipynb` or `apply-analysis.ipynb`
- **How long did a completed plan take, and which refreshes were slowest?** → `plan-log-analysis.ipynb` (`TF_LOG=json`) or `plan-output-analysis.ipynb` (`terraform plan -json`)
- **What drift did Terraform report in this plan?** → `plan-output-analysis.ipynb` only — `plan-log-analysis.ipynb` does not include drift
- **Why does export, plan, or apply look hung or stuck right now?** → the matching `*-hang-analysis.ipynb` notebook
- **Is Terraform spinning on the graph or stuck refreshing a resource?** → `plan-hang-analysis.ipynb` or `apply-hang-analysis.ipynb`
- **Are SDK calls retrying or hitting lots of 404s?** → any `*-hang-analysis.ipynb`; use `sdk-analysis.ipynb` for call counts on a completed run
- **How many SDK API calls were made, and to which endpoints?** → `sdk-analysis.ipynb` for counts; **`log-chomper/`** for response-time percentiles by endpoint

# Analysis notebooks

The `sdk-plan-export-notebooks` directory contains:

| Notebook | Use when |
|----------|----------|
| `whatisit.ipynb` | You are not sure what the log contains |
| `export-analysis.ipynb` | Completed export — timing and resource breakdown |
| `export-hang-analysis.ipynb` | Export appears hung — stuck resources, SDK retries, 404 storms |
| `plan-output-analysis.ipynb` | Completed plan — `terraform plan -json` UI output (includes drift) |
| `plan-log-analysis.ipynb` | Completed plan — `TF_LOG=json` trace |
| `plan-hang-analysis.ipynb` | Plan appears hung — graph waits, stuck refresh, SDK retries, 404 storms |
| `apply-analysis.ipynb` | Completed apply — timing and resource breakdown |
| `apply-hang-analysis.ipynb` | Apply appears hung — stuck resources, open RPCs, SDK retries |
| `sdk-analysis.ipynb` | SDK call counts from `SDK DEBUG` lines embedded in TF logs |

Notes:

- **`plan-output-analysis.ipynb`** parses **`terraform plan -json`** UI records (`refresh_start`, `resource_drift`, `planned_change`, etc.).
- **`plan-log-analysis.ipynb`** parses **`TF_LOG=json`** trace logs captured during plan (as in `generator/generator.py` and `TF_LOG_PATH` workflows). **`resource_drift` is not available** in this format.
- **Hang notebooks** (`*-hang-analysis.ipynb`) use the same **`TF_LOG=json`** captures but focus on stall patterns rather than completed-run timing.
- **`sdk-analysis.ipynb`** parses `SDK DEBUG` request/response pairs embedded in Terraform log output when `TF_DEBUG=JSON` is enabled.

**Not supported:** standalone `*_sdk_debug.log` files (HTTP trace format). Use SDK lines embedded in `*_tf_json.log` instead.

**Future work:** see `sdk-plan-export-notebooks/todo.ipynb` for planned enhancements (export filter delta, 429 wait timing).

# Shared library

Python helpers live in **`sdk-plan-export-notebooks/commonlib/`** (`classify_tf_log.py`, `prep_hang_data.py`, `prep_*_data.py`, `gencharts.py`, `config.py`).

# Other tools

- **`log-chomper/`** — CLI for SDK request/response pairing and **response-time percentiles by endpoint** (complements `sdk-analysis.ipynb`, which focuses on call counts).
- **`generator/`** — small script to generate many resources so there is enough activity to parse and log.
