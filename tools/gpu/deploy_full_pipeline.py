"""Install full-pipeline CPU dependencies on the user's existing SSH GPU pod."""
import argparse
import json
from pathlib import Path
import subprocess
import tempfile

from transformation.remote_pipeline import bundle_code,connection


def main(config):
    remote=json.loads(Path(config).read_text(encoding='utf-8'))['ssh']
    ssh,scp,target=connection(remote)
    with tempfile.TemporaryDirectory(prefix='transformx-deploy-') as temp:
        archive=Path(temp)/'engine.tar.gz';bundle_code(archive)
        subprocess.run([*scp,str(archive),target+':/workspace/transformx-engine-code.tar.gz'],check=True)
        subprocess.run([*ssh,'mkdir -p /workspace/transformx-engine-code && tar -xzf /workspace/transformx-engine-code.tar.gz -C /workspace/transformx-engine-code && bash /workspace/transformx-engine-code/tools/gpu/setup_full_pipeline.sh'],check=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--config',required=True)
    main(parser.parse_args().config)
