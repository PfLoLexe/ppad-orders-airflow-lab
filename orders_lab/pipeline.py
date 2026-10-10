"""Транзакционная загрузка снимков; CDM читает только DDS."""
import argparse
import hashlib
import json
import os
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from .validation import validate_snapshot

TRANSITIONS = {'created': {'paid', 'cancelled'}, 'paid': {'shipped', 'cancelled'},
               'shipped': {'delivered'}, 'delivered': set(), 'cancelled': set()}


def connect():
    import psycopg2
    return psycopg2.connect(os.environ['WAREHOUSE_DSN'])


def entries(manifest):
    manifest = Path(manifest)
    doc = json.loads(manifest.read_text(encoding="utf-8"))
    for entry in sorted(doc['snapshots'], key=lambda e: e['date']):
        path = manifest.parent / entry['file']
        raw = path.read_bytes()
        if hashlib.sha256(raw).hexdigest() != entry['sha256']:
            raise ValueError(f"Неверная контрольная сумма: {path.name}")
        payload = json.loads(raw)
        if payload['meta']['dataset_id'] != doc['dataset_id'] or payload['meta']['snapshot_date'] != entry['date']:
            raise ValueError('Метаданные снимка не совпадают с манифестом')
        yield entry, payload


def lock(cur):
    cur.execute("SELECT pg_advisory_xact_lock(840214)")
    cur.execute("SET LOCAL TIME ZONE 'Europe/Moscow'")


def load_stg(manifest):
    import psycopg2.extras
    with connect() as conn:
        for entry, payload in entries(manifest):
            with conn.cursor() as cur:
                lock(cur)
                dataset = payload['meta']['dataset_id']
                cur.execute('SELECT dataset_id FROM ops.dataset')
                existing = cur.fetchone()
                if existing and existing[0] != dataset:
                    raise ValueError('Хранилище уже содержит другой набор; автоматического сброса нет')
                cur.execute('INSERT INTO ops.dataset(dataset_id) VALUES (%s) ON CONFLICT DO NOTHING', (dataset,))
                cur.execute('SELECT source_sha256 FROM ops.batches WHERE snapshot_date=%s', (entry['date'],))
                existing = cur.fetchone()
                if existing and existing[0].strip() != entry['sha256']:
                    raise ValueError('Попытка изменить уже загруженный снимок')
                cur.execute('INSERT INTO stg.snapshots(snapshot_date,dataset_id,payload,source_sha256) VALUES (%s,%s,%s,%s) ON CONFLICT DO NOTHING',
                            (entry['date'], dataset, psycopg2.extras.Json(payload), entry['sha256']))
                cur.execute('INSERT INTO ops.batches(snapshot_date,dataset_id,source_sha256,stg_loaded_at) VALUES (%s,%s,%s,now()) ON CONFLICT DO NOTHING',
                            (entry['date'], dataset, entry['sha256']))
            conn.commit()


def immutable(cur, table, pk, values):
    # Имена таблиц/полей поступают только из кода, значения — параметры SQL.
    columns = list(values)
    cur.execute(f"SELECT {','.join(columns)} FROM {table} WHERE {pk}=%s", (values[pk],))
    old = cur.fetchone()
    if old is not None:
        if tuple(values[c] for c in columns) != tuple(old):
            raise ValueError(f'Изменение неизменяемой записи {table}: {values[pk]}')
    else:
        cur.execute(f"INSERT INTO {table} ({','.join(columns)}) VALUES ({','.join(['%s']*len(columns))})", tuple(values.values()))


def apply_dds(cur, data):
    validate_snapshot(data)
    for cat in data['categories']:
        immutable(cur, 'dds.categories', 'category_id', {'category_id': cat['id'], 'category_name': cat['name']})
    city_ids = {}
    for customer in data['customers']:
        if customer['city'] not in city_ids:
            cur.execute('SELECT city_id FROM dds.cities WHERE city_name=%s', (customer['city'],))
            row = cur.fetchone()
            if not row:
                cur.execute('INSERT INTO dds.cities(city_name) VALUES (%s) RETURNING city_id', (customer['city'],))
                row = cur.fetchone()
            city_ids[customer['city']] = row[0]
        city = city_ids[customer['city']]
        immutable(cur, 'dds.customers', 'customer_id', dict(customer_id=customer['id'], last_name=customer['last_name'], first_name=customer['first_name'], patronymic=customer['patronymic'], city_id=city))
    for p in data['products']:
        cur.execute('INSERT INTO dds.products VALUES (%s) ON CONFLICT DO NOTHING', (p['id'],))
        cur.execute('SELECT product_name,category_id,catalog_price,valid_from FROM dds.product_versions WHERE product_id=%s AND valid_to IS NULL', (p['id'],))
        old = cur.fetchone()
        attributes = (p['name'], p['category_id'], Decimal(p['catalog_price']))
        timestamp = datetime.fromisoformat(p['updated_at'])
        if old and tuple(old[:3]) == attributes:
            if old[3] != timestamp:
                raise ValueError('Время версии изменено без изменения атрибутов товара')
            continue
        if old:
            if timestamp <= old[3]:
                raise ValueError('Немонотонное время версии товара')
            cur.execute('UPDATE dds.product_versions SET valid_to=%s WHERE product_id=%s AND valid_to IS NULL', (timestamp,p['id']))
        cur.execute('INSERT INTO dds.product_versions(product_id,product_name,category_id,catalog_price,valid_from) VALUES (%s,%s,%s,%s,%s)', (p['id'],*attributes,timestamp))
    for o in data['orders']:
        created = datetime.fromisoformat(o['created_at'])
        immutable(cur, 'dds.orders', 'order_id', dict(order_id=o['id'],customer_id=o['customer_id'],created_at=created))
        cur.execute('SELECT status_code,valid_from FROM dds.order_status_versions WHERE order_id=%s AND valid_to IS NULL', (o['id'],))
        old = cur.fetchone()
        changed = datetime.fromisoformat(o['status_updated_at'])
        if old and old[0] == o['status']:
            if old[1] != changed:
                raise ValueError('Время статуса изменено без перехода')
        else:
            if not old and (o['status'] != 'created' or changed != created):
                raise ValueError('Для нового заказа требуется первый снимок со статусом created')
            if old:
                if o['status'] not in TRANSITIONS[old[0]] or changed <= old[1]:
                    raise ValueError('Недопустимый переход статуса')
                cur.execute('UPDATE dds.order_status_versions SET valid_to=%s WHERE order_id=%s AND valid_to IS NULL', (changed,o['id']))
            cur.execute('INSERT INTO dds.order_status_versions(order_id,status_code,valid_from) VALUES (%s,%s,%s)', (o['id'],o['status'],changed))
        for item in o['items']:
            cur.execute('SELECT product_version_id FROM dds.product_versions WHERE product_id=%s AND valid_from<=%s AND (valid_to>%s OR valid_to IS NULL)', (item['product_id'],created,created))
            version = cur.fetchone()
            if not version:
                raise ValueError('Нет версии товара на момент оформления заказа')
            vals = (version[0],item['quantity'],Decimal(item['unit_price']))
            cur.execute('SELECT product_version_id,quantity,unit_price FROM dds.order_items WHERE order_id=%s AND line_no=%s', (o['id'],item['line_no']))
            old_item = cur.fetchone()
            if old_item and tuple(old_item) != vals:
                raise ValueError('Состав или цена оформленного заказа изменены')
            if not old_item:
                cur.execute('INSERT INTO dds.order_items VALUES (%s,%s,%s,%s,%s)', (o['id'],item['line_no'],*vals))
        cur.execute('SELECT count(*),sum(quantity*unit_price) FROM dds.order_items WHERE order_id=%s', (o['id'],))
        count,total = cur.fetchone()
        if count != len(o['items']) or total != Decimal(o['total_amount']):
            raise ValueError('Несовпадение позиций и суммы заказа')


def load_dds(manifest):
    with connect() as conn:
        for entry, _ in entries(manifest):
            with conn.cursor() as cur:
                lock(cur)
                cur.execute('SELECT dds_loaded_at FROM ops.batches WHERE snapshot_date=%s', (entry['date'],))
                row = cur.fetchone()
                if not row:
                    raise ValueError('Сначала требуется STG')
                if row[0]:
                    continue
                cur.execute('SELECT max(snapshot_date) FROM ops.batches WHERE dds_loaded_at IS NOT NULL')
                latest = cur.fetchone()[0]
                if latest and str(latest) >= entry['date']:
                    raise ValueError('Новые снимки DDS должны идти по времени')
                cur.execute('SELECT payload FROM stg.snapshots WHERE snapshot_date=%s', (entry['date'],))
                apply_dds(cur, cur.fetchone()[0])
                cur.execute('UPDATE ops.batches SET dds_loaded_at=now() WHERE snapshot_date=%s', (entry['date'],))
            conn.commit()


def load_cdm(manifest):
    sql = (Path(__file__).resolve().parents[1] / 'sql/02_cdm.sql').read_text(encoding="utf-8")
    with connect() as conn:
        for entry, _ in entries(manifest):
            with conn.cursor() as cur:
                lock(cur)
                cur.execute('SELECT dds_loaded_at,cdm_loaded_at FROM ops.batches WHERE snapshot_date=%s', (entry['date'],))
                row = cur.fetchone()
                if not row or not row[0]:
                    raise ValueError('Сначала требуется DDS')
                if row[1]:
                    continue
                cur.execute(sql, {'day': entry['date']})
                cur.execute('UPDATE ops.batches SET cdm_loaded_at=now() WHERE snapshot_date=%s', (entry['date'],))
            conn.commit()


def quality(layer):
    with connect() as conn, conn.cursor() as cur:
        lock(cur)
        if layer == 'dds':
            for table, key in [('product_versions','product_id'),('order_status_versions','order_id')]:
                cur.execute(f'SELECT count(*) FROM dds.{table} a JOIN dds.{table} b ON a.{key}=b.{key} AND a.valid_from<b.valid_from AND (a.valid_to IS NULL OR a.valid_to>b.valid_from)')
                if cur.fetchone()[0]:
                    raise ValueError('Пересекаются интервалы SCD2')
            cur.execute('SELECT count(*) FROM dds.orders o WHERE NOT EXISTS (SELECT 1 FROM dds.order_items i WHERE i.order_id=o.order_id) OR NOT EXISTS (SELECT 1 FROM dds.order_status_versions s WHERE s.order_id=o.order_id AND s.valid_to IS NULL)')
            if cur.fetchone()[0]:
                raise ValueError('Заказы без позиций или текущего статуса')
        elif layer == 'cdm':
            cur.execute('SELECT count(*) FROM cdm.order_snapshots o WHERE order_amount<>(SELECT sum(i.line_amount) FROM cdm.order_item_snapshots i WHERE i.order_id=o.order_id AND i.snapshot_date=o.snapshot_date) OR (status_code=\'delivered\' AND (delivered_at IS NULL OR delivery_hours<0))')
            if cur.fetchone()[0]:
                raise ValueError('Неверная сумма или срок доставки CDM')
        else:
            raise ValueError('Неизвестный слой')
    return {'layer':layer,'passed':True}


def main():
    p = argparse.ArgumentParser()
    p.add_argument('action', choices=['stg','dds','cdm','check-dds','check-cdm','all'])
    p.add_argument('--manifest', default=os.environ.get('DATASET_MANIFEST'))
    a=p.parse_args()
    actions={'stg':load_stg,'dds':load_dds,'cdm':load_cdm}
    if a.action=='all':
        for name in ['stg','dds','check-dds','cdm','check-cdm']:
            if name.startswith('check-'): print(quality(name[6:]))
            else: actions[name](a.manifest)
    elif a.action.startswith('check-'): print(quality(a.action[6:]))
    else: actions[a.action](a.manifest)

if __name__=='__main__': main()
