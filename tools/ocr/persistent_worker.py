"""Single serial GPU OCR session. Private local paths only; no network listener."""
import argparse,json,os,socket,threading,time
from pathlib import Path
from transformation.ocr.service import encode,receive
from tools.ocr.model_pool import ModelPool


def checked_paths(message,root):
    source=Path(message['source']).resolve();output=Path(message['output']).resolve()
    if not source.is_relative_to(root) or not output.is_relative_to(root):
        raise ValueError('OCR paths must stay within the worker jobs root.')
    if not source.is_file() or source.stat().st_size>50_000_000 or output.exists():
        raise ValueError('Expected a bounded source and a new output directory.')
    if source.parent==root or output.parent==root:
        raise ValueError('OCR paths require a job subdirectory.')
    return source,output


def serve(path,root,parent,idle_seconds=300,processor=None):
    path=Path(path);root=Path(root).resolve();pool=ModelPool()
    if path.parent.stat().st_mode & 0o077:raise ValueError('OCR socket directory must be private.')
    stop=threading.Event()
    def watch_parent():
        while not stop.wait(1):
            if os.getppid()!=parent:os._exit(125)
    watcher=threading.Thread(target=watch_parent,daemon=True);watcher.start()
    last=time.monotonic()
    try:
        with socket.socket(socket.AF_UNIX) as server:
            server.bind(str(path));os.chmod(path,0o600);server.listen(4);server.settimeout(1)
            while time.monotonic()-last<idle_seconds:
                try:connection,_=server.accept()
                except socket.timeout:continue
                with connection:
                    connection.settimeout(5);timer=None
                    try:
                        message=receive(connection)
                        if message.get('action')=='health':
                            connection.sendall(encode({'ready':True}));continue
                        if message.get('action')!='ocr':raise ValueError('Unknown OCR action.')
                        timeout=message['timeout']
                        if type(timeout) not in (float,int) or not 0<timeout<=1200:raise ValueError('Invalid OCR deadline.')
                        source,output=checked_paths(message,root)
                        language=message.get('language','unknown')
                        if not isinstance(language,str) or len(language)>32:raise ValueError('Invalid source language.')
                        # A timed-out native predictor cannot be reused safely. Exit
                        # only this OCR child; never terminate Ollama or replay a task.
                        timer=threading.Timer(timeout,lambda:os._exit(124));timer.daemon=True;timer.start()
                        if processor is None:
                            from tools.ocr.run_gpu_ocr import run
                            processor=run
                        before=pool.metrics();started=time.monotonic()
                        processor(source,output,language,model_pool=pool)
                        after=pool.metrics();metrics={'seconds':time.monotonic()-started,
                            'model_loads':after['model_loads']-before['model_loads'],
                            'model_reuse_hits':after['model_reuse_hits']-before['model_reuse_hits'],
                            'model_load_seconds':after['model_load_seconds']-before['model_load_seconds']}
                        connection.sendall(encode({'status':'completed','metrics':metrics}))
                    except Exception as exc:
                        try:connection.sendall(encode({'status':'failed','error_type':type(exc).__name__}))
                        except OSError:pass
                    finally:
                        if timer:timer.cancel()
                        last=time.monotonic()
    finally:
        stop.set();path.unlink(missing_ok=True)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--socket',type=Path,required=True);parser.add_argument('--root',type=Path,required=True)
    parser.add_argument('--parent',type=int,required=True);parser.add_argument('--idle-seconds',type=int,default=300)
    args=parser.parse_args()
    if not 10<=args.idle_seconds<=3600:parser.error('Idle lifetime must be 10..3600 seconds.')
    serve(args.socket,args.root,args.parent,args.idle_seconds)


if __name__=='__main__':main()
