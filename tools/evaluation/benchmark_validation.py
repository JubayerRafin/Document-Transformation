"""Compare complete validation findings on saved PDFs; no inference or edits."""
import argparse,hashlib,json,platform,statistics,time
from pathlib import Path
from unittest.mock import patch
from transformation.validation.output_validation import inspect_output


def brute(rectangles):
    return ((i,j) for i in range(len(rectangles)) for j in range(i+1,len(rectangles)))


def compare(path,repeats=1):
    data=path.read_bytes();indexed=[];reference=[]
    for attempt in range(repeats):
        # Alternate order for repeated CPU microbenchmarks.
        results={}
        for mode in (('indexed','reference') if attempt%2==0 else ('reference','indexed')):
            tick=time.perf_counter()
            if mode=='reference':
                with patch('transformation.validation.overlap_index.candidate_pairs',side_effect=brute):
                    result=inspect_output(data,'pdf')
                reference.append(time.perf_counter()-tick)
            else:
                result=inspect_output(data,'pdf');indexed.append(time.perf_counter()-tick)
            results[mode]=result
        if results['indexed']!=results['reference']:
            raise AssertionError('Validation findings differ: '+str(path))
    return {'pdf':str(path.resolve()),'sha256':hashlib.sha256(data).hexdigest(),
            'findings_identical':True,'indexed_seconds':statistics.median(indexed),
            'reference_seconds':statistics.median(reference),'repeats':repeats,
            'layout_findings':len(results['indexed']['layout_review_candidates'])}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('pdfs',type=Path,nargs='+');parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--repeats',type=int,choices=range(1,6),default=1)
    args=parser.parse_args()
    if args.output.exists():raise ValueError('Keep previous results; choose a new output path.')
    rows=[]
    for path in args.pdfs:
        result=compare(path,args.repeats);rows.append(result)
        print(json.dumps(result),flush=True)
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps({'scope':'Saved-artifact validation only; not end-to-end PDF runtime.',
        'platform':platform.platform(),'python':platform.python_version(),'documents':rows},indent=2))


if __name__=='__main__':main()
