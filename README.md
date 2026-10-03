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
├── data/                       # CSV data files (can be replaced via UI)
│   ├── data_agg_rus.csv        # Aggregate demographic + incidence + mortality
│   └── data_ind.csv            # Individual staging records (age, stage, aggressiveness)
├── templates/
│   └── index.html              # Web dashboard with Plotly charts + sensitivity section
├── python_port_analysis.tex    # Scientific analysis with equations and validity critique
├── model_for_dummies.tex       # Plain-language guide (no formulas)
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
- **Data file management:** Upload and validate new CSV datasets through the UI; switch data sources on-the-fly
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

## License

Academic use. See original C# repository: [Magisterbes/BespalovPhd](https://github.com/Magisterbes/BespalovPhd)