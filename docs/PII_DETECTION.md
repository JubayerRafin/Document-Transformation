# PII detection for PDFs (structure-based)

`transformation/validation/pii_document.py` runs document-level detection for PDF
jobs. It uses document structure and a category policy, never words, prefixes or
layouts taken from particular test files.

## Steps

1. **Detection units.** Native pages use whole PyMuPDF text blocks (paragraphs and
   cells with their real line breaks). OCR'd pages use OCR lines. A value wrapped
   across lines is therefore seen as one value. A model value that differs from the
   source only by spaces/line breaks is mapped back to the exact source span; any
   other normalization is still rejected.
2. **Per-unit detection** (`privacy.detect`): format patterns (email, phone,
   labelled credentials) plus the local model. Requests run concurrently
   (`pii_workers`, default 4) and are merged in unit order.
3. **Field labels.** Label/value pairs come from structure only:
   "label: value" segments in a line (segments split at `|` or wide gaps), a label
   block followed by a value block on the same line (forms), table column headers,
   and a left-hand label column. One model call per document classifies each
   distinct label into a category or `none`. Values under sensitive labels are
   added. A value seen only under a table column classified `none` (e.g. product
   code or price columns) is not reported as an identifier/account by a model
   guess; inline labels never suppress anything, so one label mistake cannot hide
   PII.
4. **Name pass.** One model call per ~6000 characters lists every person name;
   each verbatim match in any unit becomes a finding.
5. **Category check.** Format validators override the model's category
   (email syntax, IPv4, phone shapes), then only the user-selected categories are
   kept. Selecting only "Identifiers" no longer removes phone numbers.

## Policy

Category meaning lives in `privacy.CATEGORY_DEFINITIONS` and is shared by every
prompt. "Identifiers" includes person/account/record numbers (employee, member,
customer, policy, case, tenant, document, invoice), user and device names, IP
addresses and credentials; it excludes product codes, SKUs, model numbers,
prices, amounts, quantities and dates. Person names include public figures.
Change policy there, not in code paths.

## Redaction geometry

Native pages are redacted from their own glyph positions; extractor boxes are not
used there because some extractors report media-box coordinates on pages with an
offset crop box. OCR'd/image pages place boxes from, in order: the whole line,
two-model word boxes, ink-gap word splitting on the page as displayed (rotated
pages included), then the whole line (audited `label_preserved: false`). Boxes are
padded by ~12% of line height so descenders inside cleared image areas go too.

## Checks

The rendered-output privacy check counts an unreadable OCR region only if it could
hide a selected value: blank areas that are not solid redaction boxes, text sharing
a long run with a selected value, or email/phone shapes. Ordinary text with digits
no longer blocks acceptance. A selected multi-word name with one word removed and
another left is reported as `partial_name_residuals`.

## Runtime

Measured on an RTX 4090 pod (Qwen 3.5 9B, PaddleOCR GPU), PII-only, 12 PDFs:
the average server time per page went from 78.7 s to under 15 s with unchanged or
better detection. What made the difference, in order of effect:

- **One OCR process per job** (`remote_worker.ocr_session`, `persistent_ocr` for
  hosted workers): Paddle models load once instead of once per page/figure/check.
  If the session has exited, a call falls back to a one-off process.
- **Ollama parallel requests**: start Ollama with `OLLAMA_NUM_PARALLEL=8`,
  `OLLAMA_FLASH_ATTENTION=1`, `OLLAMA_KV_CACHE_TYPE=q8_0`, `OLLAMA_KEEP_ALIVE=30m`
  (`tools/gpu/setup_transformx.sh`). Detection sends up to 8 requests at once
  (`pii_workers`), and the label and name passes run alongside.
- **Minified JSON answers**: about half the generated tokens, same findings.
- **Native tables for PII-only jobs** (`pii_table_strategy`, default `native`):
  Docling's table model cost 3–15 s per document on CPU and is only needed for
  translation layout.
- **Figures**: processed 3 at a time, skip their own rendered-output check (the
  page check covers them) and keep the original image when nothing was masked.
- **Rendered-output check reuse**: an output line whose text was already sent to
  the model is checked with format patterns only; changed or new lines still go to
  the model.

Batching several units into one model request was tried and rejected: the model
then flagged table headers and prices.

## Limits

The name pass and label classifier are model calls and can be wrong; names that
are common words may be over-masked where they appear capitalized. Faces and
signatures are not detected. Translation + PII and non-Latin OCR were not part of
the 2026-11-12 evaluation.
