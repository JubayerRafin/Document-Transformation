"""Local review interface. Start with: python -m streamlit run app.py"""
import hashlib
import importlib.util
import json
from pathlib import Path

from PIL import Image, ImageDraw
import streamlit as st

from extraction.pipeline import export_zip, run_pipeline, write_json
from extraction.saved_runs import discover_saved_runs

ROOT = Path(__file__).resolve().parent
RUNS = ROOT / "runs"
COLORS = {"text": "#3475db", "table": "#db8a1b", "figure": "#188b70", "raster_page": "#9b65cf"}

st.set_page_config(page_title="Document Extraction Lab", page_icon="◫", layout="wide")
st.markdown("""<style>
.stApp {background:#f7f8fa;}
.block-container {padding-top:2.2rem; max-width:1540px;}
h1 {letter-spacing:-1.5px; font-weight:750;}
[data-testid="stMetric"] {background:white;border:1px solid #e4e7eb;border-radius:12px;padding:12px 18px;}
[data-testid="stSidebar"] {background:#eef1f5;}
</style>""", unsafe_allow_html=True)

with st.sidebar:
    st.markdown("### ◫ Extraction Lab")
    st.caption("DOCUMENT TRANSFORMATION · CAPSTONE")
    st.divider()
    st.markdown("**One document. One shared pipeline.**")
    st.caption("Compare extraction, inspect the regions, and give each teammate the same evidence.")
    backend = st.selectbox("Extraction tool", ["docling", "native"], format_func=lambda s: {"native": "PyMuPDF · native reference", "docling": "Docling · unified layout + OCR"}[s])
    ocr = st.checkbox("Read text in images with OCR", value=True) if backend == "docling" else False
    if backend == "native":
        st.caption("This reference reads digital PDF content. It does not run OCR or detect complete semantic figures.")
    else:
        st.caption("Uses local model files. First run can take several minutes. Missing models produce an error, not a silent fallback.")
    upload = st.file_uploader("Upload a PDF", type=["pdf"])
    st.caption("Up to 50 MB / 100 pages. Uploaded text is data, never application instructions.")
    with st.expander("Local model settings"):
        artifacts = st.text_input("Docling artifacts folder (optional)")
        ocr_models = st.text_input("RapidOCR models folder (optional)")
    extract_clicked = st.button("Extract document", type="primary", disabled=upload is None, width="stretch")
    st.divider()
    saved = discover_saved_runs(ROOT)
    if saved:
        choice = st.selectbox("Previous runs", range(len(saved)), format_func=lambda i: f'{saved[i][1]["source_name"]} · {saved[i][1]["backend"]} · {saved[i][0].relative_to(ROOT).as_posix()}')
        if st.button("Open saved run", width="stretch"):
            st.session_state["run_dir"] = str(saved[choice][0])
    st.caption("Local research prototype · extraction phase")

st.caption("WORKSPACE / EXTRACTION")
st.title("Understand the whole page.")
st.write("Text, tables and figures—connected in one document representation.")

if extract_clicked:
    # Never use an uploaded filename as a filesystem path.
    incoming = ROOT / "uploads"
    incoming.mkdir(exist_ok=True)
    payload = upload.getvalue()
    if len(payload) > 50 * 1024 * 1024:
        st.error("Please upload a PDF smaller than 50 MB.")
        st.stop()
    source = incoming / f"{hashlib.sha256(payload).hexdigest()}.pdf"
    source.write_bytes(payload)
    st.session_state.pop("run_dir", None)
    with st.status("Extracting your document…", expanded=True) as status:
        progress = st.empty()
        try:
            run_dir, data, manifest = run_pipeline(source, RUNS, backend,
                {"ocr": ocr, "artifacts_path": artifacts or None, "ocr_model_dir": ocr_models or None}, progress.write)
            # Preserve display name only; it is never used to build a path.
            data["source_name"] = Path(upload.name.replace("\\", "/")).name
            manifest["source_name"] = data["source_name"]
            write_json(run_dir / "document.json", data)
            write_json(run_dir / "manifest.json", manifest)
            st.session_state["run_dir"] = str(run_dir)
            status.update(label="Extraction complete · ready for review", state="complete", expanded=False)
        except Exception as exc:
            status.update(label="Extraction could not complete", state="error")
            st.error(str(exc))
            st.info("For an immediate digital-PDF reference, select PyMuPDF. For Docling, check the local model setup in README.md.")

if "run_dir" not in st.session_state:
    if saved:
        st.info("Open a saved sample run from the sidebar, or upload a document to begin.")
    else:
        st.info("Upload a PDF in the sidebar to begin. No sample results or accuracy scores are simulated.")
    a, b, c = st.columns(3)
    a.markdown("#### 01 · Extract\nRun one tool across the entire document.")
    b.markdown("#### 02 · Inspect\nReview shared page regions and extracted content.")
    c.markdown("#### 03 · Compare\nSave findings before choosing your baseline.")
    st.stop()

run_dir = Path(st.session_state["run_dir"])
data = json.loads((run_dir / "document.json").read_text(encoding="utf-8"))
manifest = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
st.markdown(f"**{data['source_name']}**")
st.caption(f"{data['backend']} {data['backend_version']} · {manifest['seconds']:.2f}s including previews · IR {data['schema_version']}")
metrics = st.columns(5)
for column, label, value in zip(metrics, ["Pages", "Text regions", "Tables", "Figures", "Review warnings"],
    [len(data["pages"]), manifest["regions"].get("text", 0), manifest["regions"].get("table", 0), manifest["regions"].get("figure", 0), manifest["warnings"]]):
    column.metric(label, value)
inspect_tab, findings_tab, compare_tab, evaluation_tab, export_tab = st.tabs(["Page inspector", "Team findings", "Run comparison", "Evaluation", "Export & IR"])

with inspect_tab:
    control, filters = st.columns([1, 3])
    page_index = control.selectbox("Page", range(len(data["pages"])), format_func=lambda i: str(data["pages"][i]["number"]))
    kinds = filters.multiselect("Visible region types", list(COLORS), default=list(COLORS))
    page = data["pages"][page_index]
    visible = [r for r in page["regions"] if r["kind"] in kinds]
    left, right = st.columns([1.55, 1], gap="large")
    with right:
        st.markdown("#### Extracted regions")
        selected = st.selectbox("Inspect a region", [None] + [r["id"] for r in visible],
            format_func=lambda value: "All visible regions" if value is None else next(f'{r["id"]} · {r["kind"]} · {r["text"][:50]}' for r in visible if r["id"] == value))
        region = next((r for r in visible if r["id"] == selected), None)
        if region:
            st.caption(f"Bounds: {', '.join(f'{n:.1f}' for n in region['bbox'])} points")
            if region["text"]:
                st.text(region["text"])
            if region["cells"]:
                st.dataframe(region["cells"], width="stretch")
            if region["asset"]:
                st.image(str(run_dir / region["asset"]), caption="Region crop from the original page")
            for warning in region["warnings"]:
                st.warning(warning)
            with st.expander("Region metadata"):
                st.json(region["metadata"])
        else:
            st.caption("Select a region to read its text, inspect a table, or see its figure crop.")
            st.dataframe([{"Region": r["id"], "Type": r["kind"], "Content": r["text"][:100] or (f'{len(r["cells"])} rows' if r["cells"] else "Visual region")} for r in visible], hide_index=True, width="stretch")
        for warning in page["warnings"]:
            st.warning(warning)
    with left:
        with Image.open(run_dir / page["preview"]) as source_image:
            preview = source_image.convert("RGB")
        draw = ImageDraw.Draw(preview)
        sx, sy = preview.width / page["width"], preview.height / page["height"]
        for r in visible:
            x0, y0, x1, y1 = r["bbox"]
            draw.rectangle([x0 * sx, y0 * sy, x1 * sx, y1 * sy], outline=COLORS[r["kind"]], width=5 if r["id"] == selected else 2)
            if r["id"] == selected:
                draw.text((x0 * sx + 3, max(0, y0 * sy - 13)), r["id"], fill=COLORS[r["kind"]])
        st.image(preview, width="stretch")
        st.caption("Blue: text · Amber: table · Green: figure · Purple: full-page raster. Geometry follows the displayed page.")

with findings_tab:
    st.markdown("#### Turn observations into team tasks")
    st.caption("Findings are saved with this run. They do not change extracted content or count as ground truth.")
    review_path = run_dir / "reviews.json"
    reviews = json.loads(review_path.read_text(encoding="utf-8")) if review_path.exists() else []
    with st.form("review"):
        owner = st.selectbox("Work area", ["Text / OCR", "Tables", "Figures", "Integration / IR"])
        reference = st.selectbox("Location", ["Whole document"] + [r["id"] for p in data["pages"] for r in p["regions"]])
        severity = st.selectbox("Priority", ["Normal", "High", "Low"])
        note = st.text_area("What needs investigation?", placeholder="Example: the chart caption appears as body text and has no figure relationship.")
        if st.form_submit_button("Save finding"):
            if note.strip():
                reviews.append({"area": owner, "region": reference, "priority": severity, "finding": note.strip()})
                write_json(review_path, reviews)
                st.success("Finding saved.")
            else:
                st.warning("Write a finding before saving.")
    if reviews:
        st.dataframe(reviews, hide_index=True, width="stretch")

with compare_tab:
    st.markdown("#### Same source, different extraction runs")
    st.caption("Counts and elapsed time are diagnostics—not accuracy scores. One tool may split a paragraph into many regions.")
    matching = [(p, m) for p, m in saved if m["source_sha256"] == data["source_sha256"]]
    rows = [{"Tool": m["backend"], "Version": m.get("backend_version"), "OCR requested": m.get("options", {}).get("ocr", False) if m["backend"] == "docling" else False,
             "Seconds": m["seconds"], "Text": m["regions"].get("text", 0), "Tables": m["regions"].get("table", 0), "Figures": m["regions"].get("figure", 0), "Warnings": m["warnings"]} for _, m in matching]
    if rows:
        st.dataframe(rows, hide_index=True, width="stretch")
    else:
        st.info("Open this saved run again after extracting the same PDF with another tool.")

with evaluation_tab:
    from evaluation.ui import render_panel
    render_panel(data, run_dir, ROOT)

with export_tab:
    for warning in data["warnings"]:
        st.info(warning)
    a, b = st.columns(2)
    a.download_button("Download document IR", (run_dir / "document.json").read_bytes(), "document.json", "application/json", width="stretch")
    b.download_button("Download run + crops + findings", export_zip(run_dir), f"{run_dir.name}.zip", "application/zip", width="stretch")
    st.caption("The archive contains document content. It is an extraction result, not a redacted document.")
    with st.expander("Shared document representation"):
        st.json(data, expanded=False)
    with st.expander("Run manifest"):
        st.json(manifest)
