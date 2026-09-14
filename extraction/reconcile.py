from .models import intersection_fraction


def reconcile(document):
    """Flag interactions for review; never delete uncertain text automatically."""
    for page in document.pages:
        tables = [r for r in page.regions if r.kind == "table"]
        figures = [r for r in page.regions if r.kind == "figure"]
        for region in page.regions:
            if region.kind != "text":
                continue
            for table in tables:
                if intersection_fraction(region.bbox, table.bbox) > .65:
                    region.metadata.setdefault("overlaps_tables", []).append(table.id)
                    region.warnings.append("Text overlaps a table; check whether it is duplicated or a table label.")
            for figure in figures:
                if intersection_fraction(region.bbox, figure.bbox) > .65:
                    region.metadata.setdefault("inside_figures", []).append(figure.id)
    document.validate()
    return document
