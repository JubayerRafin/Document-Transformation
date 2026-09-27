# Reference annotation workspace

Open index.html in your browser. Everything runs locally; it sends no document data to a server.

## Start here

1. Choose Sample03.pdf, page 1. It is related to the already reviewed Sample02, but inspect this original independently.
2. Type all visible non-table text in reading order. Include headers, footers, captions, repeated occurrences and text inside figures. Exclude table-cell text. Join line wraps, but preserve punctuation and spelling.
3. Choose Table or Figure and drag over each complete region. Coordinates are in PDF points, not screen pixels. Fill each table's cells as a rectangular JSON array. Use null for covered merged-cell positions consistently with the shared policy. Do not label a full scanned page as a figure simply because it is a bitmap.
4. Complete every selected page. Save in the browser while working and download the draft as a portable backup. Browser saving does not update the files in drafts/.
5. Replace the corresponding file in drafts/ with your downloaded annotation. Keep reviewed=false until another teammate has checked every selected task against the source.
6. Only after that review, set reviewed=true and reviewed_by to the actual reviewer's name, and copy the approved file to the shared references directory. Do not mark empty templates as reviewed.

An empty tables/figures list means verified absence once approved. It does not mean unfinished work. You can explicitly disable an unfinished task in the JSON tasks list, but everyone must evaluate the exact same frozen scope.

## Scope

- All pages of Sample01, Sample03, Sample04, Sample05 and Sample06: 7 pages.
- Manual annotations and manual page images are not included in this shared workspace. The local experiment selected original PDF pages 14, 21, 35, 95, 160 and 230.
- Five source-specific sample draft files, covering seven pages.
- Unselected pages remain in the reference inventory with tasks=[]; do not delete them. The evaluator requires prediction pages to exist in the reference inventory.
- Sample02 remains unchanged and approved in references/Sample02.json.
- YOLOv3 is outside this annotation batch.

The sample selection is a small development set, not an accuracy estimate for unseen documents. Selection.json records the scope and source hashes. No predictions were used to populate the draft answers.

## Evaluate after review

Use one approved reference and its matching document.json prediction first. Do not point the evaluator at all 25 manual predictions when only six section references are being evaluated: extra source documents are rejected. Do not point it at annotation-workspace itself; selection.json is metadata, not a reference.

Use the lab's Evaluation tab to upload the approved reference for the selected run, or run main.py evaluate with --references set to that reference and --predictions set to the matching document.json. For an aggregate benchmark, gather exactly the matching prediction documents in a separate evaluation input folder and use the same approved references for every tool.

## Boundaries

The interface exports unreviewed JSON only. It validates basic box and table shape, but cannot verify factual correctness, completeness, or reading order. No new accuracy scores have been generated. The existing evaluator and approved Sample02 reference were not modified.
