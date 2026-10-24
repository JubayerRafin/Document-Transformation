"""Translate verified OCR slots in isolated raster figures; no layout redetection."""
import json
from pathlib import Path
import sys


def main(folder):
    from transformation.llm.ollama_native import translate_paragraph
    from transformation.llm.translation_guard import validate_candidate
    from transformation.pdf.runner import LANGUAGES
    from extraction.context_bridge import key
    run=Path(folder)
    manifest=json.loads((run/'manifest.json').read_text(encoding='utf-8'))
    slots=json.loads((run/'verified-slots.json').read_text(encoding='utf-8'))
    context=json.loads((run/'extraction-context.json').read_text(encoding='utf-8'))
    language=next(k for k,v in LANGUAGES.items() if v==manifest['target_language'])
    translations={};errors={}
    for slot in slots:
        source=slot['text']
        try:
            text,stats=translate_paragraph(manifest['endpoint'],manifest['model'],source,language,
                manifest['protected_values'],manifest['document_context']+context.get(key(source),''),
                structured=bool(context) and len(source)<=200,interpret_field=True,
                source_language=manifest['source_language'],terminology=manifest.get('terminology'))
            validate_candidate(source,text,manifest['protected_values'])
            translations[slot['id']]={'source_text':source,'text':text,'stats':stats}
        except (ValueError,OSError,TimeoutError) as exc:
            errors[slot['id']]={'type':type(exc).__name__,'message':str(exc)}
    (run/'region-translations.json').write_text(json.dumps(translations,ensure_ascii=False),encoding='utf-8')
    (run/'region-errors.json').write_text(json.dumps(errors),encoding='utf-8')
    if slots and not translations:
        raise RuntimeError('All figure translations failed validation; source must be preserved.')
    from transformation.pdf.scan_reconstruction import reconstruct
    reconstruct(run,run/'source.pdf')


if __name__=='__main__':main(sys.argv[1])
