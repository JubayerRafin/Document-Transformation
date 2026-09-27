# Shared PDF datasets

`provided/` contains all six original sample PDFs (8 pages total), with names normalized to Sample01.pdf through Sample06.pdf. File bytes are unchanged. SHA-256 hashes and page counts are recorded in config/docling-provided-dataset.json.

Use `samples/provided` as the batch input, not the entire samples directory: the historical samples/Sample02.pdf is the same document and would otherwise be processed twice.

`extra/YOLOv3.pdf` is the six-page public paper from https://arxiv.org/pdf/1804.02767. Its hash matches the source hash in the teammate's provisional YOLOv3 reference. Provenance is recorded in config/docling-extra-dataset.json. The provisional reference has not been adopted into the approved benchmark.

The NHTSA TrafficSF download was attempted from the official website but returned HTTP 403. It is not included in this batch; request the exact source file from the teammate before adding it.

Every tool should use the same source bytes and a fixed recorded configuration. Extract first; score only against approved references. Do not construct ground truth by copying model output.
