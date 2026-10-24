"""Recover a completed SSH job after transport failure without new inference."""
import argparse,json,re,subprocess,time
from pathlib import Path
from tools.evaluation.dataset import signature,registry,save
from transformation.remote_pipeline import connection,unpack


def recover(config,phase,document):
    root=Path(config['output_root']).resolve();index_path=root/phase/'index.json'
    index=json.loads(index_path.read_text(encoding='utf-8'))
    if index['configuration_sha256']!=signature(config):raise ValueError('Code/config changed; preserve the failed batch.')
    registry(config)
    records=[r for r in index['records'] if r['document_id']==document]
    if len(records)!=1 or records[0]['status']!='failed':raise ValueError('Choose one failed document transport record.')
    record=records[0];manifest=Path(record['manifest']).resolve();folder=manifest.parent
    if not folder.is_relative_to(root/phase/'runs'):raise ValueError('Job manifest is outside the selected batch.')
    failed=json.loads(manifest.read_text(encoding='utf-8'))
    job=failed.get('execution',{}).get('remote_job','')
    if not re.fullmatch(r'/workspace/transformx-full-jobs/[0-9a-f]{32}',job):raise ValueError('Missing verified job path.')
    ssh,scp,target=connection(config['execution_remote'])
    status=subprocess.check_output([*ssh,f'cat {job}/exit.status'],text=True,timeout=25).strip()
    if status!='0':raise ValueError('Worker has not completed successfully; no new inference started.')
    backup=folder/'transport-recovery';backup.mkdir(exist_ok=True)
    for path,name in ((manifest,'failed-manifest.json'),(index_path,'index-before-recovery.json')):
        dest=backup/name
        if dest.exists():raise ValueError('Recovery backup already exists; inspect it before retrying.')
        dest.write_bytes(path.read_bytes())
    started=time.monotonic();archive=folder/'result.zip'
    subprocess.run([*scp,target+':'+job+'/result.zip',str(archive)],check=True,timeout=240)
    result=unpack(archive,folder,job,record['source_sha256'])
    devices=result.get('execution',{}).get('ollama_models',[])
    model=config['translation_model'] if phase=='translation' else config['pii_model']
    verified=any(m.get('name')==model and m.get('size',0)>0 and m.get('size_vram',0)>=m['size']*.95 for m in devices)
    if result['status']!='generated' or not verified:raise ValueError('Recovered result lacks generated output or GPU evidence.')
    inner=Path(result['run_dir'])/'manifest.json'
    if not inner.is_file():raise ValueError('Recovered pipeline manifest is missing.')
    result['remote_job']=job
    result['transport_total_seconds']=None
    result['transport_recovery']={'original_failure_manifest':str(backup/'failed-manifest.json'),
        'retrieval_seconds':round(time.monotonic()-started,3),'archive_bytes':archive.stat().st_size,
        'processing_repeated':False,'total_timing_scope':'Interrupted transfer; no comparable end-to-end time reported.'}
    save(manifest,result)
    record.update(status='generated',manifest=str(inner),gpu_verified=True,device_evidence=devices,
                  stage_seconds={},error=None,recovered_transport=True,transport_failure_manifest=str(backup/'failed-manifest.json'))
    save(index_path,index)
    print(json.dumps({'document':document,'status':'generated','server_seconds':result['seconds'],
                      'new_inference':False,'gpu_verified':True,'manifest':str(manifest)}),flush=True)


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--config',required=True)
    p.add_argument('--phase',choices=['translation','pii'],default='translation');p.add_argument('--document',required=True)
    a=p.parse_args();recover(json.loads(Path(a.config).read_text(encoding='utf-8')),a.phase,a.document)
