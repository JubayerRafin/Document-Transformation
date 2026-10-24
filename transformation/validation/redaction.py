"""User-selected exact text removal; not automatic PII discovery."""
from pathlib import Path
import pymupdf


def redact_selected(source, destination, values):
    values=list(dict.fromkeys(v.strip() for v in values if v.strip()))
    if not values:
        raise ValueError('Enter exact values to remove.')
    if Path(source).resolve()==Path(destination).resolve():
        raise ValueError('Redacted output must be a separate file.')
    with pymupdf.open(source) as pdf:
        matches={v:[] for v in values}
        for page in pdf:
            for value in values:
                matches[value].extend((page.number,rect) for rect in page.search_for(value))
        if any(not hits for hits in matches.values()):
            raise ValueError('At least one selected value has no searchable match. It may be image text or translated differently. No redacted copy was produced.')
        for hits in matches.values():
            for page_number,rect in hits:
                pdf[page_number].add_redact_annot(rect,fill=(0,0,0))
        for page in pdf:
            page.apply_redactions(images=2, graphics=2, text=0)
        # A new output omits ancillary payloads; original is retained separately.
        pdf.set_metadata({})
        pdf.del_xml_metadata()
        for name in pdf.embfile_names():
            pdf.embfile_del(name)
        result=pdf.tobytes(garbage=4,deflate=True,clean=True)
    with pymupdf.open(stream=result,filetype='pdf') as verified:
        if any(page.search_for(value) for page in verified for value in values):
            raise ValueError('Selected text remains searchable; redaction verification failed.')
    Path(destination).write_bytes(result)
    return {'selected_values':len(values),'matched_regions':sum(len(v) for v in matches.values()),
            'searchable_text_check':'passed','review_status':'requires_visual_review',
            'limitations':'Only selected searchable occurrences were removed. Unrecognized image text, other PII and visual completeness are not verified.'}
