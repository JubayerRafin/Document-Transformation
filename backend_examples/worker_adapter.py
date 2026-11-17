"""Call the existing engine from a queue worker, never from an async HTTP route.

The backend must implement authentication, queueing, persistence and publishing.
No server credentials or host paths are accepted in client options.
"""
from dataclasses import dataclass
from pathlib import Path

from transformation.workflows import run_workflow
from transformation.validation.privacy import CATEGORIES


@dataclass(frozen=True)
class EngineSettings:
    run_root: Path
    babeldoc_python: str
    translation_endpoint: str = 'http://127.0.0.1:11435/v1'
    pii_endpoint: str = 'http://127.0.0.1:11435/v1'
    ocr_remote: dict | None = None
    timeout_seconds: int = 3600
    max_upload_bytes: int = 50_000_000  # Proposed service policy, not an engine limit.


MODES = {'translation': 'Translation only', 'pii': 'PII only', 'combined': 'Translation + PII'}
CLIENT_KEYS = {'operation', 'source_language', 'target_language', 'output_format',
               'protected_values', 'document_context', 'pii_categories'}


def execute(data, filename, request, settings, emit, publish):
    """Synchronous worker example. publish(Path) returns an opaque artifact ID.

    emit receives filtered progress. Persist detailed engine events privately in
    the real worker, along with job/owner/run associations and error diagnostics.
    """
    if not isinstance(request, dict) or set(request) - CLIENT_KEYS:
        raise ValueError('Only document operation options may be supplied by a client.')
    operation = request.get('operation')
    if operation not in MODES:
        raise ValueError('Choose translation, pii or combined.')
    if not isinstance(filename, str) or '/' in filename or '\\' in filename:
        raise ValueError('Use a display filename without path components.')
    kind = Path(filename).suffix.lower().lstrip('.')
    if kind not in {'pdf', 'docx', 'pptx'}:
        raise ValueError('Unsupported input format.')
    if not isinstance(data, bytes) or not 0 < len(data) <= settings.max_upload_bytes:
        raise ValueError('Empty or oversized upload.')
    output = request.get('output_format', kind)
    # First backend milestone: preserve native formats. Cross-format export is
    # an explicit later feature, rather than an implied fidelity guarantee.
    if output != kind:
        raise ValueError('This example enables only same-format output.')
    source = request.get('source_language', 'en')
    target = request.get('target_language')
    if operation != 'pii' and (not isinstance(target, str) or not target):
        raise ValueError('Translation requires target_language.')
    categories = request.get('pii_categories', [])
    if not isinstance(categories, list) or any(not isinstance(c, str) or c not in CATEGORIES for c in categories):
        raise ValueError('Invalid PII categories.')
    if operation != 'translation' and not categories:
        raise ValueError('Select PII categories.')
    if operation == 'translation' and categories:
        raise ValueError('Use pii or combined when selecting PII categories.')
    context = request.get('document_context', '')
    protected = request.get('protected_values', [])
    if not isinstance(context, str) or len(context) > 8000:
        raise ValueError('Invalid document context.')
    if not isinstance(protected, list) or len(protected) > 200 or any(not isinstance(v, str) or not v or len(v) > 500 for v in protected):
        raise ValueError('Invalid protected values.')
    terminal = None
    for event in run_workflow(data, filename, MODES[operation], root=settings.run_root,
            python=settings.babeldoc_python, translation_endpoint=settings.translation_endpoint,
            pii_endpoint=settings.pii_endpoint, translation_model='translategemma:12b', pii_model='qwen3.5:9b',
            source=source, target=source if operation == 'pii' else target, output_kind=output,
            protected_values=protected, document_context=context, pii_categories=categories,
            table_strategy='docling', visual_analysis=False, ocr_remote=settings.ocr_remote,
            timeout=settings.timeout_seconds):
        terminal = event
        emit({'stage': event['stage'], 'engine_status': event['status'], 'elapsed_seconds': event['seconds']})
    if not terminal or terminal['status'] != 'generated':
        # Return generic public failure. The actual service must save terminal
        # and its error privately before returning this public error code.
        return {'status': 'failed', 'error_code': 'PROCESSING_FAILED', 'review_status': 'not_reviewed'}
    artifact = Path(terminal['output_file']).resolve()
    if not artifact.is_relative_to(settings.run_root.resolve()) or not artifact.is_file():
        raise ValueError('Engine output is outside the configured job storage.')
    artifact_id = publish(artifact)
    return {'status': 'succeeded', 'artifact_id': artifact_id, 'review_status': 'requires_review'}
