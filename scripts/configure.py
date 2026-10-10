"""Генерирует локальные секреты, не выводя их в терминал."""
import argparse
import json
import os
import secrets
from pathlib import Path
root=Path(__file__).resolve().parents[1]
path=root/'.env'
parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('--manifest', help='Путь к выбранному manifest.json относительно корня проекта')
args=parser.parse_args()
manifest=None
if args.manifest:
    manifest=(root/args.manifest).resolve()
    try: manifest.relative_to(root)
    except ValueError: raise SystemExit('Manifest должен находиться внутри проекта')
    if not manifest.is_file(): raise SystemExit('Указанный manifest.json не найден')
if path.exists():
    existing=dict(line.split('=',1) for line in path.read_text(encoding='utf-8').splitlines() if line and not line.startswith('#'))
    if manifest and (root/existing['DATASET_MANIFEST']).resolve()!=manifest:
        raise SystemExit('Этот .env настроен на другой набор данных. Создайте отдельную копию стенда; инструкция: docs/scaling.md')
    if os.environ.get('COMPOSE_PROJECT_NAME') and existing.get('COMPOSE_PROJECT_NAME','orders-lab')!=os.environ['COMPOSE_PROJECT_NAME']:
        raise SystemExit('Имя Compose-проекта отличается от сохранённого. Используйте отдельную копию стенда.')
    print('.env уже существует; пароли сохранены')
else:
    if manifest is None:
        manifests=sorted((root/'data/snapshots').glob('*/manifest.json'))
        if len(manifests)!=1: raise SystemExit('Найдено несколько наборов или нет снимков. Укажите --manifest путь/manifest.json')
        manifest=manifests[0]
    names=['POSTGRES_PASSWORD','AIRFLOW_DB_PASSWORD','WAREHOUSE_PASSWORD','METABASE_DB_PASSWORD','BI_PASSWORD','AIRFLOW_PASSWORD','METABASE_ADMIN_PASSWORD','AIRFLOW_SECRET_KEY','AIRFLOW_JWT_SECRET']
    values={name:secrets.token_hex(32) for name in names}
    values['DATASET_MANIFEST']=manifest.relative_to(root).as_posix()
    path.write_text(''.join(f'{k}={v}\n' for k,v in values.items()), encoding="utf-8")
    path.chmod(0o600)
    private=root/'private'
    private.mkdir(exist_ok=True)
    auth=private/'passwords.json'
    auth.write_text(json.dumps({'admin':values['AIRFLOW_PASSWORD']}), encoding="utf-8")
    auth.chmod(0o644)  # Контейнер Airflow читает файл под своим UID.
    print('Созданы .env и private/passwords.json; значения не выводились')

# Дополнить новые технические параметры без смены действующих паролей.
values=dict(line.split('=',1) for line in path.read_text(encoding="utf-8").splitlines() if line and not line.startswith('#'))
changed=False
for name in ['METABASE_SESSION_SECRET','METABASE_ENCRYPTION_SECRET']:
    if name not in values:
        values[name]=secrets.token_hex(32);changed=True
if 'COMPOSE_PROJECT_NAME' not in values:
    values['COMPOSE_PROJECT_NAME']=os.environ.get('COMPOSE_PROJECT_NAME','orders-lab');changed=True
if 'AIRFLOW_UID' not in values:
    values['AIRFLOW_UID']=str(getattr(os, "getuid", lambda: 50000)());changed=True
if len(values.get('AIRFLOW_JWT_SECRET',''))<64:
    values['AIRFLOW_JWT_SECRET']=secrets.token_hex(32);changed=True
if changed:
    path.write_text(''.join(f'{k}={v}\n' for k,v in values.items()), encoding="utf-8");path.chmod(0o600)
