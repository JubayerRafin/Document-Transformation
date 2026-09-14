import unicodedata


def normalize(value):
    return " ".join(unicodedata.normalize("NFKC", value).split())


def edit_distance(a, b):
    """Exact Levenshtein distance, including omissions and insertions."""
    if a == b:
        return 0
    if len(a) < len(b):
        a, b = b, a
    previous = list(range(len(b) + 1))
    for i, x in enumerate(a, 1):
        current = [i]
        for j, y in enumerate(b, 1):
            current.append(min(current[-1] + 1, previous[j] + 1, previous[j - 1] + (x != y)))
        previous = current
    return previous[-1]


def iou(a, b):
    intersection = max(0, min(a[2], b[2]) - max(a[0], b[0])) * max(0, min(a[3], b[3]) - max(a[1], b[1]))
    union = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - intersection
    return intersection / union if union else 0.


def match_regions(reference, prediction, threshold):
    """Deterministic maximum-cardinality matching; neighbors prefer higher IoU.

    This maximizes detection match count, not total IoU among tied assignments.
    Region identifiers break ties. A prediction can match at most one reference.
    """
    scores = [[iou(r["bbox"], p["bbox"]) for p in prediction] for r in reference]
    neighbors = {r: sorted((p for p in range(len(prediction)) if scores[r][p] >= threshold),
                           key=lambda p: (-scores[r][p], prediction[p]["id"])) for r in range(len(reference))}
    assigned = {}

    def augment(r, seen):
        for p in neighbors[r]:
            if p in seen:
                continue
            seen.add(p)
            if p not in assigned or augment(assigned[p], seen):
                assigned[p] = r
                return True
        return False

    for r in sorted(range(len(reference)), key=lambda r: reference[r]["id"]):
        augment(r, set())
    return sorted([(r, p, scores[r][p]) for p, r in assigned.items()])


def prf(tp, fp, fn):
    return {
        "tp": tp, "fp": fp, "fn": fn,
        "precision": tp / (tp + fp) if tp + fp else None,
        "recall": tp / (tp + fn) if tp + fn else None,
        "f1": 2 * tp / (2 * tp + fp + fn) if 2 * tp + fp + fn else None,
    }


def text_counts(reference, prediction):
    a, b = normalize(reference), normalize(prediction)
    return {"character_errors": edit_distance(a, b), "reference_characters": len(a),
            "word_errors": edit_distance(a.split(), b.split()), "reference_words": len(a.split())}


def table_slots(cells):
    # None is a structural placeholder and differs from an empty-string cell.
    return {(i, j): normalize(value) if value is not None else None
            for i, row in enumerate(cells) for j, value in enumerate(row)}
