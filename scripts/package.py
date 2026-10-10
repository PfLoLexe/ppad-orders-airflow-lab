"""Архив исходников, снимков и отчётов без паролей и томов БД."""
import hashlib
import json
from pathlib import Path
from zipfile import ZipFile,ZIP_DEFLATED
ROOT=Path(__file__).resolve().parents[1]
TARGET=ROOT.parent/'airflow-lab-stand.zip'
FILES=[]
for name in ['orders_lab','dags','sql','scripts','data','tests','docs']:
    FILES.extend(p for p in (ROOT/name).rglob('*') if p.is_file() and '__pycache__' not in p.parts and not p.name.endswith('.pyc'))
FILES.extend(ROOT/name for name in ['compose.yaml','README.md','.env.example','.gitignore','.gitattributes'])
for name in ['verification.md','warehouse-verification.json','bi-verification.json','metabase-artifacts.json','metabase-results.json','dashboard-top.jpg','runtime.json','fresh-start.json','volume-verification.json']:
    p=ROOT/'reports'/name
    if p.exists():FILES.append(p)
with ZipFile(TARGET,'w',compression=ZIP_DEFLATED,compresslevel=9) as z:
    for p in sorted(FILES):z.write(p,'airflow-lab/'+p.relative_to(ROOT).as_posix())
checksum=hashlib.sha256(TARGET.read_bytes()).hexdigest()
TARGET.with_suffix('.zip.sha256').write_text(checksum+'  '+TARGET.name+'\n',encoding='utf-8')
print(json.dumps({'archive':str(TARGET),'files':len(FILES),'bytes':TARGET.stat().st_size,'sha256':checksum},ensure_ascii=False))
