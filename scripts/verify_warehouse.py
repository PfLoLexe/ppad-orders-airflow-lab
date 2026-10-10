"""Сверка каждого среза с независимым расчётом из JSON, повтора и отката."""
import copy
import hashlib
import json
import os
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from orders_lab.pipeline import connect,entries,load_stg,load_dds,load_cdm,quality,apply_dds,lock
ROOT=Path(__file__).resolve().parents[1]
MANIFEST=os.environ['DATASET_MANIFEST']
TABLES=['dds.cities','dds.customers','dds.categories','dds.products','dds.product_versions','dds.orders','dds.order_items','dds.order_status_versions','cdm.order_snapshots','cdm.order_item_snapshots','cdm.daily_orders','cdm.daily_sales','ops.batches']

def fingerprint():
    result={}
    with connect() as conn,conn.cursor() as cur:
        for table in TABLES:
            cur.execute(f'SELECT row_to_json(t)::text FROM {table} t ORDER BY row_to_json(t)::text')
            rows=[r[0] for r in cur.fetchall()]
            result[table]={'count':len(rows),'sha256':hashlib.sha256('\n'.join(rows).encode()).hexdigest()}
    return result

def main():
    source=list(entries(MANIFEST))
    with connect() as conn,conn.cursor() as cur:
        for entry,data in source:
            expected={o['id']:(o['status'],Decimal(o['total_amount']),len(o['items'])) for o in data['orders']}
            cur.execute('SELECT o.order_id,o.status_code,o.order_amount,count(i.line_no) FROM cdm.order_snapshots o JOIN cdm.order_item_snapshots i USING(snapshot_date,order_id) WHERE o.snapshot_date=%s GROUP BY o.snapshot_date,o.order_id,o.status_code,o.order_amount',(entry['date'],))
            actual={r[0]:(r[1],r[2],r[3]) for r in cur.fetchall()}
            assert actual==expected,f"Расхождение среза {entry['date']}"
            cur.execute('SELECT order_id,line_no,quantity,unit_price FROM cdm.order_item_snapshots WHERE snapshot_date=%s',(entry['date'],))
            actual_items={(r[0],r[1]):(r[2],r[3]) for r in cur.fetchall()}
            expected_items={(o['id'],i['line_no']):(i['quantity'],Decimal(i['unit_price'])) for o in data['orders'] for i in o['items']}
            assert actual_items==expected_items
            cur.execute('SELECT count(*) FROM cdm.order_snapshots WHERE snapshot_date=%s AND status_code=\'delivered\' AND delivered_at<> (SELECT valid_from FROM dds.order_status_versions s WHERE s.order_id=cdm.order_snapshots.order_id AND status_code=\'delivered\')',(entry['date'],))
            assert cur.fetchone()[0]==0
        cur.execute('SELECT product_id,catalog_price,valid_from,valid_to FROM dds.product_versions ORDER BY product_id,valid_from')
        versions=cur.fetchall()
        first=[r for r in versions if r[0]==source[-1][1]['products'][0]['id']]
        expected_versions=[]
        for _,snapshot in source:
            product=snapshot['products'][0]
            version=(Decimal(product['catalog_price']),datetime.fromisoformat(product['updated_at']))
            if not expected_versions or expected_versions[-1]!=version: expected_versions.append(version)
        assert [(r[1],r[2]) for r in first]==expected_versions
        first_order=source[-1][1]['orders'][0]
        cur.execute('SELECT line_no,unit_price FROM dds.order_items WHERE order_id=%s',(first_order['id'],))
        assert dict(cur.fetchall())=={i['line_no']:Decimal(i['unit_price']) for i in first_order['items']}
    before=fingerprint()
    load_stg(MANIFEST);load_dds(MANIFEST);load_cdm(MANIFEST)
    assert fingerprint()==before,'Повторная загрузка изменила данные'
    # Изменение товара выполняется до недопустимого перехода первого заказа;
    # весь пакет обязан откатиться, включая новую товарную версию.
    invalid=copy.deepcopy(source[-1][1])
    invalid['products'][0]['catalog_price']=str(Decimal(invalid['products'][0]['catalog_price'])+Decimal('0.01'))
    invalid['products'][0]['updated_at']=source[-1][1]['meta']['snapshot_date']+'T23:00:00+03:00'
    invalid['orders'][0]['status']='created'
    invalid['orders'][0]['status_updated_at']=source[-1][1]['meta']['snapshot_date']+'T23:01:00+03:00'
    rejected=False
    try:
        with connect() as conn,conn.cursor() as cur:
            lock(cur);apply_dds(cur,invalid)
    except ValueError:
        rejected=True
    assert rejected and fingerprint()==before,'Некорректный пакет не был полностью откатан'
    with connect() as conn,conn.cursor() as cur:
        cur.execute("SELECT count(*),sum(order_amount),count(*) FILTER(WHERE status_code='delivered'),sum(order_amount) FILTER(WHERE status_code='delivered'),round(avg(order_amount) FILTER(WHERE status_code='delivered'),2),round(100.0*count(*) FILTER(WHERE status_code='cancelled')/count(*),2),round(avg(delivery_hours) FILTER(WHERE status_code='delivered'),2) FROM cdm.order_snapshots WHERE snapshot_date=(SELECT max(snapshot_date) FROM cdm.order_snapshots)")
        row=cur.fetchone()
    report={'snapshot_comparisons':len(source),'idempotence':True,'transaction_rollback':True,'checks':[quality('dds'),quality('cdm')],'tables':before,'metrics':dict(zip(['orders','ordered_rub','delivered_orders','sales_rub','average_check_rub','cancelled_percent','delivery_hours'],[str(x) for x in row])),'product_1_versions':[[str(x) if x is not None else None for x in r] for r in first]}
    (ROOT/'reports/warehouse-verification.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n', encoding="utf-8")
    print(json.dumps({k:v for k,v in report.items() if k!='tables'},ensure_ascii=False,indent=2))
if __name__=='__main__':main()
