"""Final-PDF exact-value diagnostics, separate from human quality acceptance."""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import re

import pymupdf

from transformation.llm.protected_text import EMAIL_PATTERN, normalize_protected_values


def compare_text(source_pages, output_pages, protected_values=()):
    """Compare extracted-text counts without assuming visual or semantic fidelity."""
    def count(pages, value):
        # Permit PDF line wrapping between words, but retain punctuation and case.
        # Korean particles can directly follow preserved Latin names or dates.
        # Still reject a longer ASCII identifier such as EMP-0012 for EMP-001.
        boundary = r"[A-Za-z0-9_]" if value.isascii() else r"\w"
        literal = r"\s+".join(map(re.escape, value.split()))
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
            # Source PDFs may break an ISO date after a literal hyphen.
            # Keep digits and punctuation exact; accept only a line break.
            literal = r"-(?:[ \t]*\r?\n[ \t]*)?".join(value.split('-'))
        pattern = rf"(?<!{boundary})" + literal + rf"(?!{boundary})"
        return sum(len(re.findall(pattern, text)) for text in pages)

    def emails(pages):
        counts = Counter()
        for text in pages:
            # A domain hyphen at a PDF line break is part of the address.
            text = re.sub(r"(?<=-)\r?\n[ \t]*(?=[A-Za-z0-9])", "", text)
            counts.update(EMAIL_PATTERN.findall(text))
        return counts

    values = []
    for value in normalize_protected_values(protected_values):
        source, output = count(source_pages, value), count(output_pages, value)
        values.append({"value": value, "source_count": source, "output_count": output,
                       "status": "not_found_in_source" if source == 0 else
                       "match" if source == output else "count_mismatch"})
    source_emails, output_emails = emails(source_pages), emails(output_pages)
    addresses = [{"value": value, "source_count": source_emails[value],
                  "output_count": output_emails[value],
                  "status": "match" if source_emails[value] == output_emails[value] else "count_mismatch"}
                 for value in sorted(source_emails.keys() | output_emails.keys())]
    findings = sum(item["status"] != "match" for item in values + addresses)
    checked = bool(values or addresses)
    return {"status": "requires_review" if findings else "counts_match" if checked else "no_values_checked",
            "findings": findings, "protected_values": values, "emails": addresses,
            "limitations": "Extracted-text counts only. Reading order, fragmented text and image-only text can affect detection. Matching counts do not prove correct placement, translation quality or redaction."}


def check_run(run):
    run = Path(run).resolve()
    manifest = json.loads((run / "manifest.json").read_text(encoding="utf-8"))
    if manifest.get("status") != "generated":
        raise ValueError("Only a generated run can be checked.")
    if manifest.get("pii_redaction"):
        raise ValueError("Value preservation is not a redaction check.")
    source, output = run / "source.pdf", Path(manifest["output_pdf"])
    def read(path):
        with pymupdf.open(path) as doc:
            return [page.get_text() for page in doc]
    result = compare_text(read(source), read(output), manifest.get("protected_values", ()))
    result.update(run_id=manifest["run_id"],
                  source_sha256=hashlib.sha256(source.read_bytes()).hexdigest(),
                  output_sha256=hashlib.sha256(output.read_bytes()).hexdigest())
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run", type=Path)
    parser.add_argument("--output", type=Path, help="Optional new report file; existing files are preserved.")
    args = parser.parse_args()
    result = check_run(args.run)
    rendered = json.dumps(result, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with args.output.open("x", encoding="utf-8") as file:
            file.write(rendered + "\n")
    print(rendered)


if __name__ == "__main__":
    main()
