# Documentation

Start with [the repository layout](REPOSITORY_LAYOUT.md). Guides are grouped here
without relocating referenced files or changing existing commands.

## Product and deployment

- [Customer frontend](CUSTOMER_FRONTEND.md): Next export, Studio composition and checks.
- [Cloud setup](CLOUD_SETUP.md): initial Cloudflare/Supabase setup procedure; inspect current deployment records separately.
- [Private processing workers](PRIVATE_GPU_WORKERS.md): ownership, registration and job routing.
- [Server execution](SERVER_EXECUTION.txt): full Python processing on a connected server.
- [Configuration examples](../configs/README.md): public templates versus private host settings.
- [Prototype capabilities and limits](PROTOTYPE_IMPLEMENTATION_STATUS.md).

## Engine and quality

- [Code map](CODE_MAP.txt): engine module responsibilities; includes earlier handoff context.
- [Transformation milestones](TRANSFORMATION_MILESTONES.md).
- [Deep Doc / extraction lab](EXTRACTION_LAB_GUIDE.md).
- [PaddleOCR setup](PADDLEOCR.md) and [raster policy](OCR_RASTER_POLICY.txt).
- [Detailed evaluation](DETAILED_EVALUATION.md) and [GPU dataset evaluation](GPU_DATASET_EVALUATION.md).
- [PII handoff](PII_HANDOFF.txt): earlier teammate handoff, not evidence of present deployment status.
- [Quality hardening](QUALITY_HARDENING_PLAN.txt).
- [Prediction format](PREDICTION_FORMAT.md), [evaluation contract](EVALUATION.md) and [validation](VALIDATION.md).
- [BabelDOC sources](BABELDOC_SOURCES.md).

## Performance

- [Runtime investigation](RUNTIME_OPTIMIZATION.txt).
- [Controlled experiments](PERFORMANCE_EXPERIMENTS.txt).
- [Persistent OCR and fresh-result experiments](OCR_SESSION_EXPERIMENT.txt).

Actual results live in local ignored evidence folders. Published source does not
contain worker tokens, model weights or private document outputs. Report hardware,
cache state and timing scope when quoting performance; generated PDFs are not
human-approved results.

## Historical guides

[Architecture](ARCHITECTURE.md), [team start](TEAM_START.md),
[milestone 1](MILESTONE_1_STATUS.md), [backend handoff](BACKEND_HANDOFF.txt),
the root `BABELDOC_REPORT.md`, and [archived Streamlit guide](archive/STREAMLIT_GUIDE.md)
describe earlier milestones. Use the current root README and implementation for
the current architecture.
