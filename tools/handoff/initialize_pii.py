"""Relocate a packaged translation baseline and prepare a teammate's PII config."""
import json
from pathlib import Path


def relocate(value, old_root, new_root, sources):
    if isinstance(value, dict):
        return {k: relocate(v, old_root, new_root, sources) for k, v in value.items()}
    if isinstance(value, list):
        return [relocate(v, old_root, new_root, sources) for v in value]
    if isinstance(value, str):
        if value in sources:
            return str(new_root / sources[value])
        # Change path-valued strings only, never document text embedded in prose.
        for separator in ('\\', '/'):
            prefix = old_root.rstrip('\\/') + separator
            if value.startswith(prefix):
                return str(new_root / Path(value[len(prefix):].replace('\\', '/')))
    return value


def main():
    root = Path(__file__).resolve().parents[2]
    metadata = json.loads((root / 'handoff-package.json').read_text(encoding='utf-8'))
    config_path = root / 'configs/pii-team.local.json'
    if config_path.exists():
        raise ValueError('Already initialized. Keep your existing configuration and annotations; do not overwrite them.')
    batch = root / metadata['batch_relative']
    if (batch / 'pii/index.json').exists() or (batch / 'ground-truth').exists():
        raise ValueError('Existing PII results or annotations found. Use a fresh extracted copy for relocation.')
    source_map = metadata['source_map']
    for path in batch.rglob('*.json'):
        original = json.loads(path.read_text(encoding='utf-8-sig'))
        updated = relocate(original, metadata['original_project_root'], root, source_map)
        if original != updated:
            path.write_text(json.dumps(updated, ensure_ascii=False, indent=2), encoding='utf-8')
    config = metadata['configuration']
    config['output_root'] = str(batch)
    config['babeldoc_python'] = str(root / '.venv-babeldoc/Scripts/python.exe')
    config['ocr_remote'] = {'host': '', 'port': 22, 'key': 'SET_YOUR_OWN_PRIVATE_KEY_PATH'}
    for document in config['documents']:
        document['path'] = str(root / 'samples' / (document['id'] + '.pdf'))
    from tools.evaluation.dataset import signature, registry
    registry(config)  # Verify the packaged PDFs against the frozen source hashes.
    index_path = batch / 'translation/index.json'
    index = json.loads(index_path.read_text(encoding='utf-8'))
    index['handoff_relocation'] = {'original_configuration_sha256': index['configuration_sha256'],
        'reason': 'Source paths relocated for teammate handoff; no inference rerun or review approval transferred.',
        'original_index': 'evidence/original-translation-index.json'}
    index['configuration_sha256'] = signature(config)
    index_path.write_text(json.dumps(index, ensure_ascii=False, indent=2), encoding='utf-8')
    config_path.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding='utf-8')
    print('Verified six source PDFs and relocated completed translation evidence.')
    print('Edit host, port and key in ' + str(config_path))
    print('No PII inference or annotation approval has been performed.')


if __name__ == '__main__':
    main()
