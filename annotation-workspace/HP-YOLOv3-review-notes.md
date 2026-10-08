# HP manual and YOLOv3 reference drafts

Seven drafts, all `reviewed: false` and `reviewed_by: ""`. They are drafts for an independent reviewer, not approved ground truth, and they are deliberately kept in `annotation-workspace/drafts/` (not `references/`) so they do not block `evaluate` on the approved set.

| Draft | Source | Pages in scope |
|---|---|---|
| `hp-e877-pages-011-020.json` | `hp-e877-pages-011-020.pdf` | local page 4 (original 14): labeled printer diagram and parts table |
| `hp-e877-pages-021-030.json` | `hp-e877-pages-021-030.pdf` | local page 1 (original 21): two specification tables |
| `hp-e877-pages-031-040.json` | `hp-e877-pages-031-040.pdf` | local page 5 (original 35): tables with paper-orientation illustrations |
| `hp-e877-pages-091-100.json` | `hp-e877-pages-091-100.pdf` | local page 5 (original 95): nested setup instructions |
| `hp-e877-pages-151-160.json` | `hp-e877-pages-151-160.pdf` | local page 10 (original 160): maintenance diagrams and instructions |
| `hp-e877-pages-221-230.json` | `hp-e877-pages-221-230.pdf` | local page 10 (original 230): multilingual regulatory content |
| `YOLOv3.json` | `YOLOv3.pdf` | all 6 pages |

Every HP draft keeps the full 10-page inventory; the nine pages outside the selection stay `tasks: []`. Source hashes match `config/hp-e877-10pages-dataset.json` and `config/docling-extra-dataset.json`.

## How the answers were produced

- Text came from the PDF's own text layer, with visual line wraps joined, and was checked against the rendered pages. Where the layer is missing or wrong it was transcribed from the render instead (see HP original page 230 below).
- Table and figure boxes follow the visible ink/border geometry. They were measured from the rendered page (PyMuPDF drawing and pixel measurements), then checked with overlays drawn on the source.
- No Docling, native or other tool prediction was used to fill answers. A native extraction was run afterwards in a scratch folder only to confirm that `evaluate` accepts the drafts; nothing was edited from its output and no scores are claimed.

## Decisions to verify

- **Callout numbers (HP p14).** The diagram has 15 numbered callouts. They are legible text inside a figure, so they are in the page text in numeric order. The text layer also contains an invisible duplicate "13" at the position of "15"; it is not visible and was left out.
- **Icons are figures.** Small icons count as figures, including inline ones: NOTE/TIP icons, the information and network icons in a sentence (HP p95), the EAC marks and the Taiwan mark (HP p230).
- **Composite figures.** One caption means one figure box, even with several panels: HP Figure 9-6, YOLOv3 Figures 4 and 5.
- **Text inside charts and photos.** It is included, in the order: axis label, y ticks, x ticks, x label, legend, markers, then the in-plot list. For YOLOv3 Figure 5 the box labels follow each photo top to bottom, left photo first. Those labels are only legible at high zoom. The "Method / mAP / time" list inside YOLOv3 Figures 1 and 3 is treated as figure text, not as a table.
- **HP p230.** The Kazakh and Russian paragraphs are glyph outlines with no text layer, so they were transcribed visually from 240-420 dpi crops. A script check found no mixed Latin/Cyrillic words. The "EAC" letters in the two marks are included as text.
- **Math and symbols.** Subscripts and superscripts follow the text layer, so equations read like `bx = σ(tx) + cx`. The `●` bullet glyphs in HP table cells and instructions are kept. A detached accent in the YOLOv3 references (`Doll´ar`) is written as the visible `Dollár`.
- **Hyphenation.** Line-end hyphens from soft breaks are removed (`intro-` / `duction` becomes `introduction`); real hyphens are kept (`fine-grained`, `Darknet-19`, `C.-Y. Fu`, en dashes in page ranges). A hyphenated word that continues into the next column is joined (`surprisingly`, YOLOv3 p4).
- **Reading order.** Two-column pages read left column then right column. The arXiv margin stamp (YOLOv3 p1) is placed last. Page numbers, footers and the footnote are included.
- **Tables.**
  - YOLOv3 Table 1 (Darknet-53) has a repeat-count column (`1×`, `2×`, `8×`, `8×`, `4×`) read as its own column. Each merged cell holds its value in the first row of the block and `null` in the covered rows.
  - Truly empty cells are `""`.
  - In YOLOv3 Table 3 the "Two-stage methods" and "One-stage methods" rows are ordinary rows with the other cells empty.
  - Table captions are page text, not table cells.
- **Empty lists.** An empty tables or figures list means verified absence: for example no figures on HP p21 and YOLOv3 p3 and p5, and no tables on YOLOv3 p1, p4, p5 and p6.

No evaluation scores were generated for these drafts.
