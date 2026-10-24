# Layout code and reports

- `transformation/pdf/babeldoc_worker.py`, `document_runner.py`, `runner.py`:
  BabelDOC processing and saved worker artifacts.
- `transformation/pdf/scan_reconstruction.py`, `scan_native.py`,
  `figure_worker.py`, `figure_runner.py`: scan/figure reconstruction routes.
- `transformation/pdf/ir_bridge.py`, `nativeized_ir.py`: region association and
  OCR/nativeized source mapping; external geometry is not native glyph geometry.
- `transformation/layout/table_fitting.py`, `panels.py`, `ocr_fitting.py`,
  `line_breaks.py`: adaptive fitting and placement.
- `transformation/validation/quality.py`, `integrity.py`: conservative signals
  and document integrity checks.
- `tools/evaluation/structure.py`, `layout.py`, `layout_standard.py`: saved-run
  structural audit, earlier preservation review, and IoU/SSIM supplements.

Look for `table-fitting.json`, `panel-fitting.json`, `office-fitting.json` when
relevant, `region-errors.json`, manifest scan/geometry blockers, output PDFs and
saved review findings. Report file existence, not an assumed successful stage.

Existing report sequence, with an actual registered-run config:

```powershell
python -m tools.evaluation.structure --config <config.json>
python -m tools.evaluation.layout --config <config.json>
python -m tools.evaluation.layout_standard --config <config.json> --dpi 150
```

`layout` consumes the extraction-reconstruction report and hashed ground-truth
files. `layout_standard` supplements earlier reports; matching thresholds do not
make this an independently annotated document-layout benchmark or mAP result.
These commands write reports under the configured batch root. Preserve existing
reviews/evidence before regenerating a published report.

For text covering diagrams, position IoU is insufficient. The saved-run audit
at `output/hp-overlap-evaluation-20261023/measure.py` measures the union area of
non-whitespace output character boxes intersecting source IR figure regions,
including extracted vector-diagram regions. It compares uniquely associated
source paragraph geometry and records ambiguous mappings separately. Its 10%
screening and 25% new-intrusion thresholds are explicit diagnostic choices,
not standard acceptance criteria. Figure boxes include empty margins and valid
callout labels, so inspect original/output renders before calling a candidate
a defect. Preserve raw candidates, source/output hashes, separate visual-review
provenance, per-page counts and excluded intentional callouts. Never reuse the
HP-specific review decisions for another document or claim pixel occlusion,
exhaustive recall, or human approval from this geometry check.

Focused tests:

```powershell
python -m unittest tests.test_panels tests.test_scan_reconstruction tests.test_figure_worker tests.test_integrity
python -m unittest tests.test_layout_standard tests.test_compare_batches
```

Pinned BabelDOC environment tests include `test_adaptive_slot_fitting`,
`test_ocr_field_boundaries` and `test_table_formula_fitting`. Use a metric
environment with scikit-image for SSIM. Native Office renderers are a separate
dependency and their skipped tests must not be counted as passed.

The table fitter retains source subscript/superscript sizes only for text-only
BabelDOC formulas marked `is_corner_mark`, with a stable source character ID and
known font size. Those glyphs cannot shrink below their recorded source size;
the body-text floor and every box/visual-bounds check remain in force. This does
not exempt headings, panels, unknown formulas or tiny body text. Audit
`preserved_source_corner_font_sizes` in fitting results. Test source-sized
scripts, further shrink, missing IDs, ordinary tiny text and clipping separately.

Formula-only table units need a layout font hint derived from measured source
glyph sizes; the hint must not change those glyph styles. Replay all planned
slots after a renderer failure, since a first exception can hide later blockers.

`layout/source_atoms.py` snapshots source notation and structured identifiers
before translation. Only after bounded fitting fails may the fitter retain the
original glyph geometry: expanded formula text must exactly match the source,
visible glyphs need source IDs and measured geometry, crop containment must
hold, and current neighbors must introduce no extra intersection. Ordinary
prose, changed text and vector formulas are excluded. Audit the distinct status
`preserved_source_atomic_geometry`; this is source retention, not a successful
reflow or human visual approval. Tests: `test_source_atoms` and the pinned
BabelDOC `test_table_formula_fitting` suite. Saved IL replay cannot certify
fresh translation or final PDF appearance.
