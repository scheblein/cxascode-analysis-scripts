# Introduction
As the support team for CX as Code, we are sometimes pulled in to examine a customer's environment or one of our own internal environments to understand what is happening in their Terraform/CX as Code environment.

As a result of these requests, we have written several Python and Jupyter Notebook scripts to process and analyze the data.  We are releasing these into our lab environment "AS-IS" with no guarantee of support.  Our goal is to share these scripts so that the large CX as Code community can use them as they see fit.

# Installation

All requirements were captured in a Python `requirements.txt` file.  The libraries can be
installed using `pip install -r requirements.txt`.

Start Jupyter from the **`notebooks/`** directory (or open notebooks from its workflow subfolders).

```
notebooks/
  commonlib/          # shared Python helpers
  whatisit.ipynb      # classify a capture and pick the right notebook
  sdk-plan/           # plan-analysis (STDOUT JSON) + sdk-analysis (TF_LOG + sdk_debug)
  export/             # performance-analysis + hang-analysis (TF_LOG)
  plan/               # performance-analysis + hang-analysis (TF_LOG)
  apply/              # performance-analysis + hang-analysis (TF_LOG)
```

# Configuration
Notebooks read paths from the `commonlib/config.Config` class. Outputs are written to the folder specified in TERRAFORM_LOG_PATH.

Logs are cached for ease of exploration. To reset/ignore cache, set the appropriate environment variable.

```
export TERRAFORM_LOG_PATH=""        # Input: raw capture file to analyze (required)
export FORCE_RENORMALIZE=""         # Optional: set to 1 to ignore existing cache and rebuild
export DISABLE_NORMALIZED_CACHE=""  # Optional: set to 1 to skip cache read/write (in-memory only)
```

# Pick a notebook

Set **`TERRAFORM_LOG_PATH`** to your TF_LOG capture. Pick a **workflow** and **type**:

| Workflow | **Performance** (completed run) | **Hang** (stuck / killed / partial) |
|---|---|---|
| **Export** | `export/performance-analysis.ipynb` | `export/hang-analysis.ipynb` |
| **Plan** | `plan/performance-analysis.ipynb` | `plan/hang-analysis.ipynb` |
| **Apply** | `apply/performance-analysis.ipynb` | `apply/hang-analysis.ipynb` |

**Plan deep dive** (`terraform plan -json`): `sdk-plan/plan-analysis.ipynb`

**SDK deep dive** (`TF_LOG` and `sdk_debug`): `sdk-plan/sdk-analysis.ipynb`

# Shared library

Python helpers live in **`notebooks/commonlib/`** (`classify_tf_log.py`, `prep_hang_data.py`, `prep_*_data.py`, `gencharts.py`, `config.py`).

# Other tools

- **`log-chomper/`** — CLI for SDK request/response pairing and **response-time percentiles by endpoint** (complements `sdk-plan/sdk-analysis.ipynb`, which focuses on call counts).
- **`generator/`** — small script to generate many resources so there is enough activity to parse and log.

# Reading results

**Notebooks** are for working a capture: they surface what the log shows — where a run stalled, how API traffic behaved over time, and which layer owns the problem (**terraform**, **provider**, or **sdk**).

**`*-report.json`** is what you export from a hang or performance notebook when you want that summary in a file. Analyze the same workload again (same notebook, new capture) and compare the two reports to see what changed across provider or Terraform versions — without reopening the full log.

Sidecar files, report fields, and **`issue_attribution`** are documented in **[HOW-TO-READ-RESULTS.md](HOW-TO-READ-RESULTS.md)**.
