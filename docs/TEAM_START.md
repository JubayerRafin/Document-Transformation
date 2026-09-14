# First team sprint

## The shared deliverable

Compare Docling and the native PyMuPDF reference on the same six sample PDFs. Both are implemented. Do not select a winner based on region counts.

## You: figures and integration

- Inspect the figure crops for each backend.
- Mark missing figures, fragments, extra background regions and incorrect crops.
- Distinguish full-page raster images from semantic figures.
- Check whether captions and text inside pictures are represented or missing.
- Agree on any IR changes with all members before changing the adapters.

## Text member

- Transcribe agreed reference text from representative pages.
- Compare omissions, OCR substitutions and reading order.
- Check image-contained text even when the rest of the page has selectable text.
- Record failures using the app's Team findings tab, including region IDs.

## Table member

- Annotate expected rows, columns, merged cells and cell text.
- Check body text does not repeat extracted cell content.
- Inspect table images and embedded figures, not only cell strings.
- Record failures using the same saved runs.

## Fourth member: evaluation and viewer

- Keep a record of versions, settings and sample hashes from manifests.
- Build human-checked reference annotations and agree on matching rules.
- Separate extraction accuracy from runtime and region counts.
- Extend tests for confirmed failures before changing the baseline.

## Sprint acceptance criteria

1. Every sample has a reviewed result for each backend.
2. Each member provides a short list of concrete failures with page/region references.
3. The team agrees on the first baseline and explains the tradeoff.
4. The next sprint contains targeted improvements, not a rewrite of working components.

The main Python sequence is in `extraction/pipeline.py`; native extraction and Docling conversion are in `extraction/backends/`. The IR contract is in `extraction/models.py`.
