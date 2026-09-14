from dataclasses import asdict, dataclass, field
import math


@dataclass
class Region:
    id: str
    kind: str
    bbox: list[float]
    text: str = ""
    cells: list = field(default_factory=list)
    metadata: dict = field(default_factory=dict)
    asset: str | None = None
    warnings: list[str] = field(default_factory=list)


@dataclass
class Page:
    number: int
    width: float
    height: float
    rotation: int
    preview: str = ""
    regions: list[Region] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


@dataclass
class Document:
    source_name: str
    source_sha256: str
    backend: str
    backend_version: str
    pages: list[Page] = field(default_factory=list)
    schema_version: str = "0.1.0"
    coordinate_system: str = "displayed-page points; top-left origin; bbox=[left,top,right,bottom]"
    settings: dict = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self):
        return asdict(self)

    def validate(self):
        ids = set()
        numbers = set()
        for page in self.pages:
            if page.number in numbers or page.number < 1:
                raise ValueError("Page numbers must be unique and one-based.")
            numbers.add(page.number)
            if not all(math.isfinite(n) and n > 0 for n in (page.width, page.height)):
                raise ValueError("Page dimensions must be finite and positive.")
            for region in page.regions:
                if region.id in ids:
                    raise ValueError(f"Duplicate region ID: {region.id}")
                ids.add(region.id)
                if region.kind not in {"text", "table", "figure", "raster_page"}:
                    raise ValueError(f"Unknown region kind: {region.kind}")
                if len(region.bbox) != 4 or not all(math.isfinite(n) for n in region.bbox):
                    raise ValueError(f"Invalid bounding box: {region.id}")
                x0, y0, x1, y1 = region.bbox
                if not (0 <= x0 < x1 <= page.width + .01 and 0 <= y0 < y1 <= page.height + .01):
                    raise ValueError(f"Out-of-page bounding box: {region.id}")


def clipped_box(box, width, height):
    x0, y0, x1, y1 = map(float, box)
    result = [max(0., min(width, x0)), max(0., min(height, y0)),
              max(0., min(width, x1)), max(0., min(height, y1))]
    return result if result[2] > result[0] and result[3] > result[1] else None


def intersection_fraction(a, b):
    overlap = max(0, min(a[2], b[2]) - max(a[0], b[0])) * max(0, min(a[3], b[3]) - max(a[1], b[1]))
    area = (a[2] - a[0]) * (a[3] - a[1])
    return overlap / area if area > 0 else 0
