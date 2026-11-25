# Running TransformX: operations guide

For the person who looks after the TransformX server. It covers installing, starting and stopping,
updating, what happens when something stops, where the logs are, and what to do when something goes
wrong. Everything runs on one Linux server with an NVIDIA GPU; there is no outside service, account
system or database. Documents never leave the server unless a model on another machine is added on
purpose (`configs/transformx.toml`, `[models] remote_servers`).

All commands run on the server, as the user that installed TransformX (usually `root`), from the
application folder:

```bash
cd /workspace/transformx-app
```

## What runs on the server

| Part | What it does | Port (local only) | Log |
|---|---|---|---|
| Website and engine (`tools/service/local_server.py`) | The website, its API, and the job queue; each job runs as its own process | 8080 | `/workspace/transformx-site.log` |
| PII model server (vLLM, Qwen 3.5 9B) | Finds personal data | 8000 | `/workspace/vllm.log` |
| Translation model server (vLLM, TranslateGemma 12B) | Default translation model | 8010 | `/workspace/vllm-translation.log` |
| Higher-quality translation model (vLLM, TranslateGemma 27B), optional | Offered as "TranslateGemma 27B · higher quality, slower" | 8011 | `/workspace/vllm-27b.log` |
| Watchdog (`tools/gpu/watchdog.sh`) | Starts again whatever stops | — | `/workspace/transformx-watchdog.log` |
| Ollama | Fallback model runner; idle when vLLM serves both models | 11434 | `/workspace/ollama.log` |

The model servers listen on `127.0.0.1` only. The website listens on `127.0.0.1:8080` unless set
otherwise (see [Who can open the website](#who-can-open-the-website)).

Jobs, uploads and results are stored in `runs/local/` inside the application folder: one folder per
job with the uploaded source, the results, `job.json` (status and options) and `process.log` (the
engine's log for that job).

## Requirements

- Linux with an NVIDIA GPU and its driver (`nvidia-smi` works). 40 GB of GPU memory or more (L40S,
  A6000, A100, H100) runs both models side by side and two jobs at a time; 24 GB (RTX 4090) works,
  with the models taking turns. The optional 27B model needs 40 GB or more.
- About 80 GB of free disk for the models and environments (about 55 GB more for 27B), plus room for
  jobs. The watchdog log warns when less than 5 GB is free.
- 64 GB of RAM or more is recommended (80 GB or more lets a sleeping model wake in about a second).
- Outbound HTTPS during installation (Python packages, model downloads). No inbound port is needed.
- Node.js 20 or newer to build the website (once per update; it can also be built on another
  computer, see [Updating](#updating)).

## Installing

```bash
git clone https://github.com/JubayerRafin/Document-Transformation.git /workspace/transformx-app
cd /workspace/transformx-app
bash tools/gpu/setup_transformx.sh pii      # PaddleOCR (GPU) and Ollama
bash tools/gpu/setup_full_pipeline.sh       # Deep Doc Extractor, BabelDOC, LibreOffice
bash tools/gpu/setup_vllm.sh                # vLLM and both models (checksums verified)
npm --prefix frontend ci && npm --prefix frontend run build    # the website (frontend/out)
```

For the optional higher-quality model, also run `TRANSFORMX_HIGH_QUALITY_SETUP=1 bash tools/gpu/setup_vllm.sh`.
The scripts install into `/workspace`; keep that folder. Installing takes about an hour, mostly
downloads.

Optional settings go in `configs/transformx.toml` (copy `configs/transformx.example.toml`; every
line is optional). Check the file and the model servers with:

```bash
/workspace/paddle-env/bin/python -m transformation.settings check
/workspace/paddle-env/bin/python -m transformation.settings show    # every setting and where it came from
```

## Starting

```bash
bash tools/gpu/start_site.sh
```

This starts the model servers, then the website, then the watchdog. It is safe to run again:
anything already running is left alone. The first start after installing compiles GPU kernels and
takes 5–10 minutes; later starts take 2–3 minutes.

After changing `configs/transformx.toml`, or to load new code, restart:

```bash
bash tools/gpu/start_site.sh --restart
```

A job running at that moment is stopped and shows as **interrupted**; upload the document again to
run it. To avoid that, stop with `stop_site.sh` (it waits for running jobs) and then start.

### Starting when the server boots

`start_site.sh` does not survive a reboot by itself. On a server with systemd, create
`/etc/systemd/system/transformx.service`:

```ini
[Unit]
Description=TransformX
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
RemainAfterExit=yes
WorkingDirectory=/workspace/transformx-app
EnvironmentFile=-/etc/transformx.env
ExecStart=/bin/bash tools/gpu/start_site.sh
ExecStop=/bin/bash tools/gpu/stop_site.sh --now --models
TimeoutStartSec=1800

[Install]
WantedBy=multi-user.target
```

then `systemctl daemon-reload && systemctl enable --now transformx`. Settings such as
`TRANSFORMX_PASSWORD=...` go in `/etc/transformx.env` (readable by root only). This unit has not
been tried on a systemd machine yet; check `systemctl status transformx` after the first boot.

On a container host without systemd (RunPod and similar), set the container's start command to
`bash /workspace/transformx-app/tools/gpu/start_site.sh; sleep infinity`, or run `start_site.sh`
after each restart of the container.

## Stopping

```bash
bash tools/gpu/stop_site.sh              # waits until no job is running, then stops the website
bash tools/gpu/stop_site.sh --now        # stops at once (a running job shows as interrupted)
bash tools/gpu/stop_site.sh --models     # also stops the model servers and frees the GPU
```

`stop_site.sh` pauses the watchdog first (it creates `/workspace/transformx-paused`), so nothing is
started again behind your back. `start_site.sh` ends the pause.

## When something stops: the watchdog

Every minute the watchdog checks that the website answers and that each model server answers. A
problem must be seen on 3 checks in a row (about 3 minutes) before it acts, so a busy server is not
restarted for one slow answer. Then:

- **website stopped or not answering**: `start_site.sh --restart` (also starts any stopped model
  server). A job that was running shows as interrupted; upload the document again.
- **a model server stopped**: only that server is started again (`start_worker.sh --servers-only`);
  the website and running jobs are left alone. Jobs that needed the model meanwhile retry, or fail
  with a message saying the model was unavailable.

Each problem and each restart is written to `/workspace/transformx-watchdog.log` with the time. The
watchdog does not fix a cause (a full disk, a broken GPU): if the same restart repeats, read the
logs below. Settings: `TRANSFORMX_WATCHDOG=0` (start without it), `TRANSFORMX_WATCHDOG_SECONDS`
(60), `TRANSFORMX_WATCHDOG_FAILURES` (3), `TRANSFORMX_MIN_FREE_GB` (5).

Is it running? `pgrep -af watchdog.sh`. Quick health check by hand:

```bash
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8080/v1/system    # 200 (or 401 with a password)
curl -s http://127.0.0.1:8000/v1/models | head -c 200; echo                  # PII model
curl -s http://127.0.0.1:8010/v1/models | head -c 200; echo                  # translation model
nvidia-smi                                                                   # GPU memory in use
df -h /workspace                                                             # free disk
```

## Who can open the website

By default the website listens on `127.0.0.1` only: users reach it through an SSH tunnel,

```bash
ssh -L 8080:localhost:8080 user@<server>      # then open http://localhost:8080
```

To open it on the company network instead, set a password and the address to listen on, then
restart:

```bash
export TRANSFORMX_PASSWORD='<a long password>'   # every visitor is asked for it (any user name)
export TRANSFORMX_HOST=0.0.0.0                   # or the server's own address
bash tools/gpu/start_site.sh --restart
```

`start_site.sh` refuses to listen beyond `127.0.0.1` without a password. Everyone who has the
password sees all jobs: there are no separate user accounts. The password travels in plain HTTP
unless a reverse proxy with HTTPS (nginx, Caddy, the company's own) is put in front; use one on
any network that is not fully trusted.

## Updating

Wait until no job is running, or stop with `stop_site.sh`, then:

```bash
cd /workspace/transformx-app
git pull
npm --prefix frontend ci && npm --prefix frontend run build
bash tools/gpu/start_site.sh --restart
```

`--restart` also restarts the pre-loaded job processes, so every job afterwards runs the new code
(without it, a job can mix old and new code). If the server has no Node.js, build the website on
another computer (`npm --prefix frontend ci && npm --prefix frontend run build`) and copy the
`frontend/out` folder into the application folder on the server. If an update changes a setup
script, its notes say so; run that script again before restarting.

After an update, run one short document of each job type (translation, PII, translation + PII)
before telling users.

## Backups and disk space

- **Jobs** (`runs/local/`) hold the uploaded documents and their results. Back it up if results must
  be kept; it holds personal data, so keep backups as protected as the server. Finished jobs are
  not deleted automatically: delete old job folders when they are no longer needed (each folder is
  one job; deleting it removes it from the website after the next restart).
- **Settings**: `configs/transformx.toml` and any key files it names.
- The models and environments in `/workspace` can be downloaded again with the setup scripts; no
  backup is needed.

## Troubleshooting

| What you see | Likely cause | What to do |
|---|---|---|
| The website does not open | Site stopped, or the SSH tunnel is closed | `pgrep -af local_server.py`; `tail -50 /workspace/transformx-site.log`; reopen the tunnel; `bash tools/gpu/start_site.sh` |
| `start_site.sh` waits a long time on the first start | vLLM compiles GPU kernels the first time (5–10 min) | Wait; later starts reuse them |
| "vLLM on port 8000 did not start" | Not enough free GPU memory, often another program on the GPU | `nvidia-smi`; stop the other program; `ollama ps` and `ollama stop <model>` if Ollama holds a model; start again |
| Jobs fail with a GPU out-of-memory message | Same as above | As above; OCR falls back to the CPU on its own, slower |
| Jobs fail with "PII model unavailable" or the translation model unavailable | A model server stopped | The watchdog starts it within about 3 minutes; see `/workspace/transformx-watchdog.log` and the model's log |
| The 27B choice is missing on the upload form | 27B not installed, or it did not start (needs the GPU to itself while it loads) | `tail -30 /workspace/vllm-27b.log`; `TRANSFORMX_HIGH_QUALITY=off` starts without it |
| A job shows **interrupted** | The website restarted while it ran | Upload the document again |
| A job runs very long | Large or scanned document; the limit is 60 minutes plus 30 s per page beyond 100 | `tail runs/local/<job id>/process.log`; cancel from the website if needed |
| Many restarts in the watchdog log | A cause the restart does not fix | Read the site and model logs around the restart times; check `df -h` and `nvidia-smi` |
| A translated sentence kept in the source language | The translation checks refused every answer (numbers or codes changed) | Expected safety behaviour: the job's review list names it; report the document if it repeats |

A result is a draft for review: every job's page lists warnings and items to check before a document
is shared. See [security notes and known limitations](SECURITY_AND_LIMITATIONS.md).

## Useful files

| File | What it is |
|---|---|
| `configs/transformx.toml` | Settings (copy of `configs/transformx.example.toml`) |
| `/workspace/servers.env` | Model server addresses written at start (no keys) |
| `runs/local/<job id>/` | One job: source, results, `job.json`, `process.log` |
| `docs/ENGINE_SERVICE.md` | Every setting, the translation model options, mass testing |
| `docs/SECURITY_AND_LIMITATIONS.md` | Where documents go, secrets, known limitations |
