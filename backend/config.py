from dataclasses import dataclass
import json, os
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]

@dataclass
class Settings:
    data: Path
    database_url: str
    origins: tuple[str, ...] = ('http://127.0.0.1:8000', 'http://localhost:8000')
    secure_cookies: bool = False
    max_upload: int = 50_000_000

    @classmethod
    def load(cls):
        data = Path(os.environ.get('TRANSFORMX_DATA', PROJECT/'runs/software-v1')).resolve()
        db_file=data/'database.local.json'
        stored=json.loads(db_file.read_text(encoding='utf8')).get('url') if db_file.exists() else None
        return cls(data, os.environ.get('TRANSFORMX_DATABASE_URL') or stored or 'sqlite:///'+(data/'transformx.db').as_posix(),
                   tuple(x.strip().rstrip('/') for x in os.environ.get('TRANSFORMX_ORIGINS', 'http://127.0.0.1:8000,http://localhost:8000').split(',') if x.strip()),
                   os.environ.get('TRANSFORMX_SECURE_COOKIES', '0') == '1')

    @property
    def worker_profile(self):
        path = self.data/'worker-profile.json'
        return json.loads(path.read_text(encoding='utf-8')) if path.exists() else {'enabled':False}

    def prepare(self):
        self.data.mkdir(parents=True, exist_ok=True)
        (self.data/'jobs').mkdir(exist_ok=True)
        if not (self.data/'worker-profile.json').exists():
            (self.data/'worker-profile.json').write_text(json.dumps({'enabled':False,
                'engine_python':str(PROJECT/'.venv-paddleocr/Scripts/python.exe'),
                'execution_remote':None,'timeout_seconds':3600,
                'engine_options':{'table_strategy':'auto','pdf_page_mode':'document','http_keepalive':True,
                    'nativeized_ir_mode':'reuse','babeldoc_dual_output':False,'figure_engine':'ocr-slots',
                    'light_pdf_io':True,'ocr_cache_mode':'reuse','translation_qps':8}},indent=2),encoding='utf-8')
