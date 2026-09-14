"""Evaluate the currently opened run using the same engine as the CLI."""
import json

from .contracts import load_protocol
from .evaluate import evaluate_collection
from .workflow import report_row


def render_panel(document, run_dir, root):
    import streamlit as st
    from PIL import Image, ImageDraw
    st.markdown("#### Evaluate against a reviewed reference")
    st.caption("Upload the shared reference JSON for this PDF. Draft annotations cannot be scored. Use the command line for a complete multi-document experiment.")
    reference_upload = st.file_uploader("Reviewed reference JSON", type=["json"], key="evaluation_reference")
    if reference_upload is None:
        st.code('python main.py reference-init "sample.pdf" --output references/sample.json', language="powershell")
        st.info("Create the draft, manually annotate it, and record the reviewer before evaluating. See docs/EVALUATION.md.")
        return
    try:
        reference = json.loads(reference_upload.getvalue().decode("utf-8-sig"))
        protocol = load_protocol(root / "config/evaluation.json")
        report = evaluate_collection([reference], [document], protocol, run_dir.name)
    except (ValueError, KeyError, TypeError) as exc:
        st.error(str(exc))
        return
    st.dataframe([report_row(report)], hide_index=True, width="stretch")
    st.caption("CER/WER: lower is better. F1/IoU: higher is better. Blank scores are undefined or unannotated—not zero.")
    st.download_button("Download evaluation report", json.dumps(report, ensure_ascii=False, indent=2), "evaluation.json", "application/json")
    number = st.selectbox("Reference overlay page", [p["number"] for p in reference["pages"] if p["tasks"]])
    expected = next(p for p in reference["pages"] if p["number"] == number)
    actual = next((p for p in document["pages"] if p["number"] == number), None)
    if actual and actual.get("preview"):
        with Image.open(run_dir / actual["preview"]) as image:
            image = image.convert("RGB")
        draw = ImageDraw.Draw(image)
        sx, sy = image.width / expected["width"], image.height / expected["height"]
        for task, kind in (("tables", "table"), ("figures", "figure")):
            if task not in expected["tasks"]:
                continue
            for region in expected[task]:
                x0, y0, x1, y1 = region["bbox"]
                draw.rectangle([x0 * sx, y0 * sy, x1 * sx, y1 * sy], outline="#188b70", width=4)
            for region in actual["regions"]:
                if region["kind"] == kind:
                    x0, y0, x1, y1 = region["bbox"]
                    draw.rectangle([x0 * sx, y0 * sy, x1 * sx, y1 * sy], outline="#d98420", width=2)
        st.image(image, caption="Green: reference boxes. Amber: predicted boxes. Only annotated table/figure tasks are shown.")
    details = next(p for p in report["documents"][0]["pages"] if p["page"] == number)
    if "text" in details:
        left, right = st.columns(2)
        left.caption("Reference text")
        left.text(details["text"]["reference"])
        right.caption("Predicted text")
        right.text(details["text"]["prediction"])
    with st.expander("Matches, missed regions and extra regions"):
        st.json(details)
