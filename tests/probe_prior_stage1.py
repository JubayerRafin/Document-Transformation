"""Opt-in shadow comparison of a separately supplied Deep Doc Extractor Stage 1.

Does not change translation routing, run an LLM, or treat extraction agreement
as ground truth. Run with a Python environment containing the prior dependencies.
"""
import argparse
from collections import Counter
from dataclasses import asdict
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import sys
import time

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))


def tokens(text):
    return Counter(re.findall(r"\w+",text.casefold()))


def overlap(reference,candidate):
    expected,actual=tokens(reference),tokens(candidate)
    return {'reference_tokens':sum(expected.values()),
            'matched_tokens':sum((expected & actual).values()),
            'reference_token_recall':round(sum((expected & actual).values())/sum(expected.values()),4) if expected else None,
            'missing_tokens':list((expected-actual).elements())[:80]}


def content_text(elements):
    parts=[]
    for element in elements:
        if element.element_type=='text':
            parts.append(element.content)
        elif element.element_type=='table':
            table=json.loads(element.content)
            parts.extend(table['headers'])
            parts.extend(str(value) for row in table['rows'] for value in row.values())
    return '\n'.join(parts)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-dir',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--docling',action='store_true')
    parser.add_argument('inputs',nargs='+',type=Path)
    args=parser.parse_args()
    import pymupdf
    from extraction.backends.native import extract
    source=args.source_dir.resolve()
    spec=importlib.util.spec_from_file_location('prior_stage1',source/'__init__.py',submodule_search_locations=[str(source)])
    package=importlib.util.module_from_spec(spec)
    sys.modules['prior_stage1']=package
    spec.loader.exec_module(package)
    output=args.output.resolve(); output.mkdir(parents=True,exist_ok=True)
    config={'text':{'min_block_chars':5,'heading_font_threshold':1.2},
            'images':{'output_subdir':'images','min_width':50,'min_height':50},
            'tables':{'output_subdir':'tables','use_docling':args.docling}}
    report={'mode':'shadow_only','docling_requested':args.docling,
            'source_files':{p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in source.glob('*.py')},
            'metric_limitations':'Native text token agreement measures loss relative to a parser, not OCR or semantic accuracy. Raster-only pages have no native reference.',
            'documents':[]}
    for path in args.inputs:
        path=path.resolve()
        folder=output/path.stem; folder.mkdir(exist_ok=True)
        started=time.monotonic(); elements=[]; failures={}
        for kind,worker in [('text',package.TextExtractor(config['text'])),
                            ('image',package.ImageExtractor(config,str(folder))),
                            ('table',package.TableExtractor(config,str(folder)))]:
            try: elements.extend(worker.extract(str(path)))
            except Exception as exc: failures[kind]=f'{type(exc).__name__}: {exc}'
        elapsed=round(time.monotonic()-started,3)
        native=extract(path,hashlib.sha256(path.read_bytes()).hexdigest(),folder,{},lambda _:None)
        native.validate()
        (folder/'current-native-ir.json').write_text(json.dumps(native.to_dict(),ensure_ascii=False,indent=2),encoding='utf-8')
        (folder/'prior-elements.json').write_text(json.dumps([asdict(e) for e in elements],ensure_ascii=False,indent=2),encoding='utf-8')
        compiler=package.MarkdownCompiler()
        compiler.compile(elements,str(folder/'prior-output.md'))
        compiled_elements=compiler._deduplicate(sorted(elements,key=lambda e:(e.page_num,e.y_coordinate)))
        pages=[]
        with pymupdf.open(path) as pdf:
            for i,page in enumerate(pdf):
                page_elements=[e for e in elements if e.page_num==i+1]
                native_regions=native.pages[i].regions
                native_content='\n'.join(r.text if r.kind=='text' else '\n'.join(str(v or '') for row in r.cells for v in row) for r in native_regions)
                comparison=overlap(page.get_text(),content_text(page_elements))
                pages.append({'page':i+1,'prior_counts':dict(Counter(e.element_type for e in page_elements)),
                    'current_native_counts':dict(Counter(r.kind for r in native_regions)),
                    'prior_token_comparison':comparison,'current_native_token_comparison':overlap(page.get_text(),native_content),
                    'after_compiler_token_comparison':overlap(page.get_text(),content_text([e for e in compiled_elements if e.page_num==i+1])),
                    'tables':[{'headers':t['headers'],'data_rows':len(t['rows']),'columns':len(t['headers']),'source':t.get('source','camelot')}
                              for t in [json.loads(e.content) for e in page_elements if e.element_type=='table']]})
                page.get_pixmap(matrix=pymupdf.Matrix(1,1)).save(folder/f'source-page-{i+1}.png')
        row={'source':str(path),'sha256':hashlib.sha256(path.read_bytes()).hexdigest(),'seconds':elapsed,
             'failures':failures,'pages':pages,'geometry_contract':'Prior elements expose y only; no full bounding boxes. Not passed to reconstruction.'}
        report['documents'].append(row)
        (output/'comparison.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
        print(json.dumps(row,ensure_ascii=True),flush=True)
    # Unrelated controls for destructive transformations, not sample fixes.
    element=package.ExtractedElement
    prefix='Shared instructions '+('x'*110)
    controls=[element(1,100,'text',prefix+' first ending'),element(2,100,'text',prefix+' different ending'),
              element(1,200,'text','Repeat this instruction.'),element(2,200,'text','Repeat this instruction.')]
    compiler=package.MarkdownCompiler()
    text=package.TextExtractor(config['text'])
    report['controls']={'distinct_or_repeated_source_blocks':len(controls),
        'blocks_after_prior_deduplication':len(compiler._deduplicate(controls)),
        'numeric_field_in_body_removed_as_footer':text._is_footer_or_header('Operating capacity 4500',200,792),
        'body_heading_with_number_removed_as_footer':text._is_footer_or_header('Section 12',200,792)}
    (output/'comparison.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(report['controls']),flush=True)


if __name__=='__main__':
    main()
