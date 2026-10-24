"""Run local checks by dependency environment; no model or dataset inference."""
import argparse
import contextlib
import hashlib
import json
from pathlib import Path
import unittest

VENDOR={'test_adaptive_slot_fitting','test_ocr_field_boundaries','test_table_formula_fitting'}
METRICS={'test_compare_batches','test_layout_standard'}


def cases(suite):
    for item in suite:
        if isinstance(item,unittest.TestSuite):yield from cases(item)
        else:yield item


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--group',choices=['primary','babeldoc','metrics'],required=True)
    p.add_argument('--output',required=True);p.add_argument('--exclude-native-office',action='store_true')
    args=p.parse_args();root=Path(args.output);root.mkdir(parents=True,exist_ok=True)
    project=Path(__file__).resolve().parents[2]
    def fingerprint():
        paths=sorted(p for folder in ('transformation','extraction','tools/ocr') for p in (project/folder).rglob('*.py'))
        return hashlib.sha256(b''.join(str(p.relative_to(project)).encode()+p.read_bytes() for p in paths)).hexdigest()
    before=fingerprint()
    suite=[];excluded=[]
    for path in sorted((Path(__file__).resolve().parents[2]/'tests').glob('test*.py')):
        group='babeldoc' if path.stem in VENDOR else 'metrics' if path.stem in METRICS else 'primary'
        if group!=args.group:continue
        for test in cases(unittest.defaultTestLoader.loadTestsFromName('tests.'+path.stem)):
            if args.exclude_native_office and test.id().endswith('test_all_nine_routes_with_real_writers_and_redaction'):
                excluded.append({'test':test.id(),'reason':'Native desktop Office renderer excluded explicitly; not counted as passed.'})
            else:suite.append(test)
    with (root/(args.group+'.log')).open('w',encoding='utf-8') as log,contextlib.redirect_stdout(log),contextlib.redirect_stderr(log):
        result=unittest.TextTestRunner(stream=log,verbosity=2).run(unittest.TestSuite(suite))
    stable=fingerprint()==before
    record={'group':args.group,'tests_run':result.testsRun,'passed':result.wasSuccessful() and stable,
        'production_code_sha256':before,'production_code_stable':stable,
        'failures':[{'test':test.id(),'traceback':trace} for test,trace in result.failures],
        'errors':[{'test':test.id(),'traceback':trace} for test,trace in result.errors],
        'skipped':[{'test':test.id(),'reason':reason} for test,reason in result.skipped],'excluded':excluded}
    (root/(args.group+'.json')).write_text(json.dumps(record,indent=2),encoding='utf-8')
    print(json.dumps(record,ensure_ascii=True),flush=True)
    raise SystemExit(not record['passed'])


if __name__=='__main__':main()
