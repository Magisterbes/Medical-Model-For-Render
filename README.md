# MedicalModel2024 — Render-lite build

A memory-conscious variant of the
[base MedicalModelPython app](https://github.com/Magisterbes/MedicalModelPython)
that fits small hosting tiers such as **Render's 512 MB free plan**.

The simulation core is unchanged: with the same seed and population this build
produces **byte-for-byte identical results**. What differs is the runtime
budget policy, the defaults and the Docker packaging.

## Why a separate build?

A 1M-agent simulation needs ~1.3 GB of RAM, and the base app keeps ~200 MB
resident before it even starts simulating:

| Population | Peak RSS |
|-----------:|---------:|
| — (imports only) | 201 MB |
| 50 000 | 303 MB |
| 100 000 | 354 MB |
| 300 000 | 564 MB |
| 1 000 000 | 1 294 MB |

Render compute plans: **Free / Starter = 512 MB**, **Standard = 2 GB**.

## What this build does differently

1. **Memory guard** (`model/memory.py`) — reads the container's cgroup v2/v1
   memory limit and rejects oversized requests with a clear **HTTP 400**
   instead of letting the platform OOM-kill the process.
2. **cgroup-aware parallelism** — the sensitivity analysis bounds its worker
   count by the CPU quota *and* the memory budget, never by `os.cpu_count()`
   (which reports *host* cores inside a container and would spawn far too many
   processes).
3. **Conservative defaults** — 20 000 agents in the UI; sensitivity uses
   30 000 agents / 3 factors. Override with `DEFAULT_POPULATION`.
4. **Lazy imports** — scikit-learn, pandas and scipy load only when actually
   needed. The idle footprint drops from ~290 MB to **~55 MB**, which matters
   because free instances spin down and cold-start often.
5. **Releases the population** after a run, keeping only the summary and stats.
6. **Slimmer image** — `matplotlib`, `plotly`, `tqdm` and `optuna` removed
   (unused on the server; the browser loads Plotly from a CDN).
   Image size 1.26 GB → **0.99 GB**.
7. **Binds to `$PORT`** as required by Render and most PaaS providers.
8. **Per-run isolation** — a single simulation slot guarded by a lock, a `run_id`
   on every result, downloads scoped to their own directory, sanitised uploads.
   See *Concurrent access* below.

## Datasets

Datasets are declared in **`data/datasets.json`** and offered as a single dropdown in
the parameter panel. Selecting one sets the aggregate *and* the staging file together,
which makes a mismatched pair (a US aggregate with German staging, say) impossible to
pick by accident; the panel shows the description, the totals and the citation stored
next to the files. Both file lists are still available under
*Advanced: choose files individually*, so uploaded CSVs keep working.

| Dataset | Aggregate | Staging | Source |
|---------|-----------|---------|--------|
| **GLOBOCAN 2022 · Colorectal · USA** *(default)* | `globocan_colorectum_usa_agg.csv` | `globocan_colorectum_usa_ind.csv` (synthetic) | GLOBOCAN 2022 (v1.1), IARC |
| **GLOBOCAN 2022 · Colorectal · Germany** | `globocan_colorectum_germany_agg.csv` | `globocan_colorectum_germany_ind.csv` (synthetic) | GLOBOCAN 2022 (v1.1), IARC |
| **Original MedicalModel2024 · Russia** | `data_agg_rus.csv` | `data_ind.csv` | shipped with the original repository |

The GLOBOCAN aggregates are **real** incidence and mortality per five-year age band,
expanded to single years of age; the `deaths all` column and the staging records are
**synthetic**, because GLOBOCAN publishes neither (see Appendix A of the guide).

| Dataset | Cases | Deaths | Population | Crude incidence |
|---------|------:|-------:|-----------:|----------------:|
| USA | 151 162 | 52 924 | 334 805 268 | 45.15 per 100 000 |
| Germany | 59 851 | 25 844 | 83 883 587 | 71.35 per 100 000 |

### Rebuilding or extending a dataset

The tools take the country as an argument — no copy-paste fork of the script is needed:

```bash
python tools/build_globocan_dataset.py --country 276 --country-name Germany \
       --out-prefix globocan_colorectum_germany --life-expectancy 80.7
python tools/make_synthetic_staging.py --prefix globocan_colorectum_germany
```

Country codes are ISO numeric (`840` USA, `276` Germany, `392` Japan, …). Then append
one entry to `data/datasets.json` and restart: the file names in the entry are all the
application needs, so no code changes are involved. An entry whose CSVs are missing from
`data/` is dropped automatically, and the appendix of `/guide` documents the same
procedure.

> Ferlay J, Ervik M, Lam F, Laversanne M, Colombet M, Mery L, Piñeros M, Znaor A,
> Soerjomataram I, Bray F (2024). *Global Cancer Observatory: Cancer Today
> (version 1.1)*. Lyon: International Agency for Research on Cancer.
> <https://gco.iarc.who.int/today>

Neither file is fetched at runtime — both are committed to the repository.

## Safe population per plan

| Plan | RAM | Safe population |
|------|-----|-----------------|
| Free / Starter | 512 MB | ~150 000 (the guard caps at 189 440) |
| Standard | 2 GB | ~1 000 000 |

The limit is resolved in this order:

1. `MEMORY_LIMIT_BYTES` (explicit — recommended on PaaS),
2. the container's cgroup v2 / v1 limit,
3. **512 MB assumed** if neither is available.

There is deliberately no "unlimited" fallback: if the limit cannot be read the
app assumes the smallest tier, because running without a budget means the guard
and the worker cap are both disabled. (Exactly that happened once: with the
cgroup unreadable, `os.cpu_count()` reported the *host's* cores, the sensitivity
analysis spawned five worker processes and Render restarted the service for
exceeding its memory limit.)

### Environment variables

| Variable | Default | Purpose |
|----------|---------|---------|
| `MEMORY_LIMIT_BYTES` | auto / 512 MB | Instance memory budget |
| `CPU_QUOTA` | auto | Number of CPUs the instance may use |
| `SIM_MAX_WORKERS` | auto | Hard cap on sensitivity worker processes |
| `DEFAULT_POPULATION` | 20000 | Population pre-filled in the UI |

Sensitivity runs are **clamped** (not rejected) to the safe population, and the
UI reports the reduction. A normal `/api/simulate` request that is too large is
rejected with HTTP 400 and a message explaining the limit.

## Deploy on Render

1. **New → Web Service**, connect this repository.
2. Language **Docker**, Branch **main**, Plan **Free**.
3. Health Check Path **`/api/status`**.
4. Or import the included `render.yaml` blueprint.

Render injects `PORT`; the container binds to it automatically.

## In-app guide

The app ships a self-contained reference page at **`/guide`** (linked from the
header) covering:

- the exact CSV format of the aggregate and staging datasets, with examples,
- a from-scratch walkthrough: upload → select → fit → simulate → sensitivity,
- uploading over the REST API (`curl` examples),
- Docker/Render caveats (uploads are ephemeral on the free plan) and a
  troubleshooting table of every validation message.

## Downloads

After each **Fit + Simulate** or **Sensitivity** run the UI shows a
**⬇ Downloads** panel. The same list is available at `GET /api/downloads`, and
each file is streamed from `GET /api/download/<run_id>/<name>` — the id is the one
the run returned, and a link carrying another run's id is refused with 404.

| Single run | Sensitivity |
|------------|-------------|
| `agents.csv` — every agent, **including healthy ones** | `agents_factor_<x>.csv` — per factor, cancer patients only |
| `agents_cancer.csv` — cancer patients only | `sens_chart_*.csv` — one file per chart |
| `chart_*.csv` — the data behind every chart | `sens_metrics.csv` — metrics per factor |
| `summary.csv`, `meta.json` | `meta.json` |

Files are streamed to `output/exports/<run_id>/` in fixed-size chunks, so the peak
extra memory stays at a few MB regardless of the population size (a 50 000-agent
history is ~3.8 MB of CSV). One directory per run means a new run can no longer
delete the files another session is still downloading; the newest
`EXPORT_DIRS_KEPT` (3) bundles are kept and older ones are pruned. On Render the
whole tree is ephemeral, like the uploads.

`meta.json` documents every agent column, and all exports use `;` as the
delimiter, matching the input files.

## Concurrent access (two visitors at once)

A single free instance is one process holding one simulation, so visitors share
it. Rather than pretend otherwise, the build **serialises the work and scopes the
results**:

| Guarantee | How |
|-----------|-----|
| Only **one** simulation at a time | The idle→busy transition is made under a lock. Previously the flag was checked and set ~20 lines apart, so two requests arriving together could both start a run — two populations in 512 MB is an OOM kill. |
| Every run has an **id** (`run_id`) | `/api/status`, `/api/results` and `/api/downloads` return it; the browser stores it per tab and ignores anything carrying a different one, so a visitor is never shown another's progress, charts or files. |
| Downloads cannot be cross-served | The URL carries the run id *and* the file must be in that run's manifest; a foreign id, or the old id-less URL, returns 404. |
| Exports cannot collide | One directory per run (`output/exports/<run_id>/`), so no run can `rmtree` another's files. |
| Uploads cannot damage the shipped data | Names are sanitised (`secure_filename`), files listed in `datasets.json` cannot be overwritten, and validation runs on a temporary file that is *moved* into place only on success — a rejected upload can no longer delete an existing file. |
| Reproducibility survives concurrency | The RNG state is thread-local, so one run cannot re-seed the stream another is drawing from. Seeded runs still produce byte-identical numbers. |

Deliberately **not** solved here: a second visitor is asked to wait rather than
queued, and `data/` uploads are still shared (same name = overwrite). Per-visitor
isolation — cookie sessions, per-user directories, a task queue — is the next
step, and it only works on a single instance: running more than one would need
shared state (Redis) and shared storage, because the run registry lives in
process memory.

## Local development

```bash
pip install -r requirements.txt
python web_app.py                            # → http://127.0.0.1:5000
python run_simulation.py --population 20000 --seed 42
```

Reproduce the 512 MB production budget locally:

```bash
docker build -t medicalmodel2024-render .
docker run --rm --memory=512m -p 5000:5000 medicalmodel2024-render
# or, using the compose file (which sets mem_limit: 512m):
docker compose up --build
```

## Notes

- Free instances have **no persistent disk**: uploaded CSVs and saved parameter
  files are lost on redeploy.
- Free instances spin down after inactivity, so the first request after a pause
  pays a cold start (Python import + Numba cache load, a few seconds).
- The web app keeps run state in module-level globals, so it must stay a single
  process (no multi-worker WSGI server).

## License

Academic use. See the original C# repository:
[Magisterbes/BespalovPhd](https://github.com/Magisterbes/BespalovPhd)


## What the Model Does

- Simulates a virtual population of up to 1 million people
- Each person can develop cancer, be diagnosed, receive treatment, and die (cancer or natural causes)
- Screening programme simulation: regular testing detects cancers earlier, improving survival
- **Sensitivity analysis:** quantifies how lead-time assumptions affect outcomes
- Compares outcomes with/without screening: lives saved, years of life gained, stage distributions

## Project Structure

```
MedicalModelPython/
├── run_simulation.py           # CLI entry point
├── web_app.py                  # Flask web server with REST API
├── Dockerfile                  # Container image (non-root, health check, Numba warm-up)
├── docker-compose.yml          # One-command run with volume mounts
├── model/                      # Core simulation engine
│   ├── simulation.py           # Main loop, annual iteration, orchestration
│   ├── population.py           # Population as Structure-of-Arrays + Numba JIT kernels
│   ├── parameters.py           # TOML config parser, data loading, sklearn logistic regression
│   ├── hazard.py               # PiecewiseHazard (diagnosis), ExpHazard (cancer death)
│   ├── gompertz.py             # Reduced Gompertz tumour growth model
│   ├── distribution.py         # Empirical CDF/PDF distributions (vectorized)
│   ├── random.py               # Reproducible RNG with seed tracking
│   ├── stats.py                # Statistics collection, rates, survival curves
│   └── sensitivity.py          # Parallel lead-time sensitivity + Savitzky-Golay smoothing
├── optimization/               # Parameter calibration
│   ├── objective_diag.py       # Diagnosis hazard (Poisson MLE, L-BFGS-B)
│   ├── objective_gompertz.py   # Gompertz growth (vectorized cross-entropy, Nelder-Mead)
│   └── objective_mort.py       # Cancer death hazard (Poisson MLE, L-BFGS-B)
├── config/
│   └── parameters.toml         # Model configuration
├── data/                       # CSV datasets (extended via the manifest, no code changes)
│   ├── datasets.json           # Manifest of the named datasets offered in the UI
│   ├── globocan_colorectum_usa_agg.csv       # GLOBOCAN 2022, USA — aggregate (default)
│   ├── globocan_colorectum_usa_ind.csv       # ... staging (synthetic)
│   ├── globocan_colorectum_germany_agg.csv   # GLOBOCAN 2022, Germany — aggregate
│   ├── globocan_colorectum_germany_ind.csv   # ... staging (synthetic)
│   ├── data_agg_rus.csv        # Original MedicalModel2024 aggregate
│   └── data_ind.csv            # Original MedicalModel2024 staging
├── tools/                      # Dataset builders (run offline, not at app runtime)
│   ├── build_globocan_dataset.py   # Fetch + expand any GLOBOCAN country into an aggregate file
│   └── make_synthetic_staging.py   # Synthesise the matching staging file
├── templates/
│   ├── index.html              # Web dashboard with Plotly charts + sensitivity section
│   └── guide.html              # Data-format guide, API examples, dataset appendix
├── python_port_analysis.tex    # Scientific analysis with equations and validity critique
├── model_for_dummies.tex       # Plain-language guide (no formulas)
├── model_logic.tex             # Modelling decisions: mechanism, justification, alternatives per element
├── model_logic_ru.tex          # The same document in Russian
├── test_fit_speed.py           # Benchmark for calibration speed
├── test_sensitivity_api.py     # Smoke test for the sensitivity API
└── requirements.txt            # Python dependencies
```

## Key Features

- **Structure-of-Arrays (SoA):** Population stored as NumPy arrays — ~10× less memory than object lists
- **Numba JIT:** Cancer history logic and screening compiled to machine code — ~50× faster
- **Vectorized operations:** Batch generation of ages, death ages, and diagnosis ages
- **Web UI:** Interactive parameter editor, CSV upload with validation, fit/simulate/save/load, sensitivity analysis
- **Sensitivity analysis:** Lead-time perturbation (×0.5…×1.5) with dedicated chart section showing:
  - Overlaid incidence, mortality, and survival curves (smoothed via Savitzky-Golay filter)
  - Filled area curves for years saved by lead-time factor
  - Stage distribution at baseline
  - Tornado bars and numeric table for aggregate metrics
- **Dataset management:** Named datasets from `data/datasets.json` (GLOBOCAN USA / Germany, legacy Russia) chosen in a single dropdown, which sets the aggregate *and* staging file together so they cannot be mismatched. New CSVs can be uploaded and validated, then selected under *Advanced: choose files individually*
- **Reproducibility:** Deterministic seeds logged for every run

## Performance

| Operation | Time | Notes |
|-----------|------|-------|
| Generate 1M population | ~1.7 s | NumPy batch + Numba |
| 1 simulation year (1M agents) | ~0.05 s | Vectorized `np.bincount` statistics |
| Calibration (Fit) | ~0.2 s | Vectorized Gompertz + L-BFGS-B / Nelder-Mead |
| Sensitivity (5 factors × 30yr × 100K) | ~2 s | Factors run in parallel across cores for heavy runs |
| Full run (30 yr × 50K) | ~3 s | Including start-up and gather stats |

## Documentation

- `python_port_analysis.tex` — Full scientific analysis with equations, identifiability critique, and calibration details
- `model_for_dummies.tex` — Plain-language description for non-specialists
- `model_logic.tex` (English) and `model_logic_ru.tex` (Russian) — the modelling-decision
  document: for every element of the model it gives the mechanism, why that design was chosen
  rather than the alternatives, and what would change if the design were switched. Each element
  ends with the rejected alternatives. Built PDFs are committed next to the sources
  (`model_logic.pdf`, `model_logic_ru.pdf`)

## License

Academic use. See original C# repository: [Magisterbes/BespalovPhd](https://github.com/Magisterbes/BespalovPhd)