# Existing translation implementation

- `transformation/llm/translategemma.py` and `ollama_native.py`: translation/model transport.
- `transformation/llm/protected_text.py`, `translation_guard.py`, `localized_numbers.py`, `role_bindings.py`: literal preservation and semantic field-binding guards.
- `transformation/llm/translation_recovery.py`: bounded fallback after two failed
  TranslateGemma attempts. Source atoms and edge punctuation remain outside the
  model; translated prose segments and the combined result both undergo the
  original guards. Recovery is marked for review in progress evidence. It does
  not return an untranslated paragraph or turn a failed job into partial success.
  Bibliographic initial/surname lists remain unchanged; month/year labels use the
  existing calendar lexicon. More than 24 prose segments or a segment over 1500
  characters fails explicitly. This fallback can cost additional requests.
  A short unchanged/wrong-script prose connector may retry once with explicit
  fragment instructions; all such calls share a 24-request recovery budget.
  Versioned identifiers with extraction whitespace after a hyphen remain exact
  source atoms; explicit literals are never normalized to make a response pass.

Failed short plain-text headings retry without neighboring context, including
mixed-case and multiword headings. Successful first requests keep their context.
Mixed-case hyphenated identifiers containing digits are protected as whole
atoms. Numeric checks permit source-backed hyphenated number words and Korean
ordinal forms; explicit protected strings still require exact preservation.
Test wrong quantities, added repetitions, altered codes and role swaps as well
as positive cases. Passing guards is not semantic or layout approval.

`localized_numbers.py` includes a narrow English-to-Korean printing equivalence:
`1-sided printing`/`2-sided printing` can become `단면 인쇄`/`양면 인쇄`. Explicit
protected values remain exact. Tests must reject wrong sides, duplicate modes,
altered identifiers/prices and unrelated omitted numbers. This is not a general
license for numeric omissions or proof of overall semantic accuracy. The policy
digest invalidates translation caches after guard changes.
- `transformation/llm/terminology.py`, `field_context.py`, `policy.py`: document terminology and context/policy.
- `transformation/pdf/ir_bridge.py`: mapping source regions to BabelDOC paragraphs.
- `transformation/validation/quality.py`: conservative review signals, not acceptance scores.
- `tools/evaluation/detailed.py`: review generation, verified region/group alignment, counts and optional COMET.
- `tools/evaluation/dataset.py`: registered inputs, source hash checks, phase execution and basic scoring.
- `tools/evaluation/regression.py`, `compare_batches.py`: saved-run comparisons.
- `docs/DETAILED_EVALUATION.md`: review schema and environment instructions.

Run commands from the repository root, substituting a real config. These report
commands write evaluation/review files into the configured output root, so retain
existing reviewed files and avoid regenerating them blindly.

```powershell
python -m tools.evaluation.detailed reviews --config <config.json> --phase translation
python -m tools.evaluation.detailed evaluate --config <config.json> --phase translation
python -m tools.evaluation.detailed prepare-metrics --config <config.json> --phase translation
```

`prepare-metrics` prepares verified pairs without neural inference. If explicitly
requested, `evaluate --comet-model Unbabel/wmt22-cometkiwi-da` scores without target
references; `Unbabel/wmt22-comet-da` requires references. Both may download model
weights. CPU is the default; `--gpus 1` means the evaluator's own machine, not an
Ollama SSH tunnel. Use the separate evaluation dependency environment.

Review artifacts include `ground-truth/`, `review-bundles/`,
`translation/detailed/report.json`, `summary.csv` and `report.html` under the
batch root. Record exact model checkpoint and pair counts. Region CER/WER
measures source extraction, not translation correctness.

Focused regression tests:

```powershell
python -m unittest tests.test_translation_guard tests.test_contact_bindings tests.test_localized_numbers
python -m unittest tests.test_translation_recovery tests.test_slide_generalization
python -m unittest tests.test_protected_pipeline tests.test_structured_translation tests.test_comet_groups tests.test_detailed_evaluation
```

The literal guard compares attached Latin units against exact source-proven
number/unit pairs (`source_unit_spacing`); this changes comparison only, never
the output text. Explicit protected spans and identifier interiors are excluded.
Check changed numbers, duplicate values and explicit-span formatting alongside
equivalent unit spacing. Literal recovery retains an uppercase metric label
only between a numeric atom and a BabelDOC formula marker; ordinary labels
still require translation. A saved-response replay is not a fresh PDF run.

Trace failures before inference as well as rejected responses. The TranslateGemma
adapter checks both source and placeholder-expanded lengths; `translation_segments`
splits oversized input losslessly at unprotected whitespace, preferring numbered
entry and sentence boundaries. Do not cut protected spans or author initials,
truncate source text, or disable the final whole-paragraph literal/binding checks.
The normal short-input path stays unchanged. Test raw-length overflow, marker
expansion, exact source coverage, indivisible tokens, bounded segment counts,
numbered entries and partial failures with `test_translation_segments`.
