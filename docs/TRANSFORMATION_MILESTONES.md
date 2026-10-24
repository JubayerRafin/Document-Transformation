# TransformX implementation milestones

Owner: Jubayer. Other team members can supply tool experiments and reviewed references independently.

| Milestone | Deliverable | Exit condition |
| --- | --- | --- |
| 1. First PDF with web interface | BabelDOC → local model → reconstructed PDF; language selector, preview and download | Sample02 translated, generated output inspected; configuration recorded |
| 2. Repeatable jobs | History, retry, stable IDs and preserved native intermediate data | Runs are reproducible and failures cannot appear approved |
| 3. Quality review | Extraction metrics plus translation/layout review | Missing content, clipping, table/figure errors are recorded per sample |
| 4. Optional PII redaction | Independent operation, candidate confirmation, removal verification | Known leaks block approval; unsupported image redaction is explicit |
| 5. Six-sample coverage | End-to-end runs for all six supplied PDFs | Every sample has an acceptance record or a demonstrated limitation |
| 6. Office functions | DOCX/PPTX ingestion and writers; legacy PPT conversion boundary | Implemented functions have basic checks; dataset acceptance remains pending |
| 7. Reliability and scale | Resume/retry where supported, long-document tests, documentation | Repeatable demonstration and handover |

Multiple language options are offered, but each pair remains experimental until evaluated. The first integration check is English → Korean. BabelDOC's full native pipeline performs PDF translation/reconstruction. The existing extraction adapter is for benchmarking, not a replacement for BabelDOC's native IR.

## Running the new workspace

The existing extraction app remains `app.py`. The new transformation workspace is `transform_app.py`.

1. Install the existing `requirements.txt` in your UI environment.
2. Create a separate Python 3.11 environment and install `requirements-transformation.txt`.
3. Start Ollama with a downloaded local model. The initial configuration uses `qwen2.5:3b`.
4. Run `python -m streamlit run transform_app.py` from the repository root.
5. In Local model setup, enter the BabelDOC environment's Python executable.
6. Select Sample02 or upload a digital PDF, select different source/target languages, and translate.

Optional environment variables:

- `TRANSFORMX_BABELDOC_PYTHON`: default interpreter shown in the UI.
- `TRANSFORMX_RUNTIME_HOME`: process-local Windows profile for BabelDOC cache isolation. Optional; ordinarily BabelDOC uses the user's normal cache.

Model/font downloads may require internet during setup. Translation requests are restricted to a local endpoint in this milestone. This restriction is not a comprehensive network isolation guarantee for third-party dependencies.

Generated PDFs are unreviewed artifacts. Opening successfully and matching the source page count do not establish translation fidelity or safe redaction. No PII redaction is performed in Milestone 1. Run folders and logs may contain document content and are excluded from Git.
