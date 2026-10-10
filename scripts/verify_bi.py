"""Проверяет семь метрик через API BI и реальные ограничения BI-роли."""
import json
from decimal import Decimal,ROUND_HALF_UP
from datetime import datetime,date as calendar_date,timedelta
from pathlib import Path
import metabase_bootstrap as mb
ROOT=Path(__file__).resolve().parents[1]

def params(values):
    return [{'id':k,'type':'date/single' if k in ('snapshot_date','date_from','date_to') else 'category','target':['variable',['template-tag',k]],'value':v} for k,v in values.items()]

def expected(snapshot,filters):
    cats={c['id']:c['name'] for c in snapshot['categories']}
    products={p['id']:p for p in snapshot['products']}
    customers={c['id']:c for c in snapshot['customers']}
    selected=[]
    for o in snapshot['orders']:
        if filters.get('city') and customers[o['customer_id']]['city']!=filters['city']:continue
        items=[i for i in o['items'] if not filters.get('category') or cats[products[i['product_id']]['category_id']]==filters['category']]
        if not items:continue
        amount=sum((Decimal(i['unit_price'])*i['quantity'] for i in items),Decimal(0))
        selected.append((o,amount))
    def inside(day):return filters.get('date_from','0001-01-01')<=day<=filters.get('date_to','9999-12-31')
    cohort=[(o,a) for o,a in selected if inside(o['created_at'][:10])]
    delivered=[(o,a) for o,a in selected if o['status']=='delivered' and inside(o['status_updated_at'][:10])]
    def rounded(n):return n.quantize(Decimal('.01'),rounding=ROUND_HALF_UP)
    num=len(cohort);sale=sum((a for _,a in delivered),Decimal(0))
    hours=[Decimal(str((datetime.fromisoformat(o['status_updated_at'])-datetime.fromisoformat(o['created_at'])).total_seconds()))/3600 for o,_ in delivered]
    hours=[h.quantize(Decimal('.0001'),rounding=ROUND_HALF_UP) for h in hours]
    return [Decimal(num),sum((a for _,a in cohort),Decimal(0)),sale,rounded(sale/len(delivered)) if delivered else None,rounded(Decimal(100)*sum(o['status']=='cancelled' for o,_ in cohort)/num) if num else None,rounded(sum(hours)/len(hours)) if hours else None]

def main():
    mb.SESSION=mb.api('POST','session',{'username':'admin@orders-lab.invalid','password':mb.ENV['METABASE_ADMIN_PASSWORD']})['id']
    artifacts=json.loads((ROOT/'reports/metabase-artifacts.json').read_text(encoding="utf-8"))
    manifest=ROOT/mb.ENV['DATASET_MANIFEST']
    source=json.loads(manifest.read_text(encoding="utf-8"))
    snapshots={e['date']:json.loads((manifest.parent/e['file']).read_text(encoding="utf-8")) for e in source['snapshots']}
    dates=sorted(snapshots)
    margin=min(4,(len(dates)-1)//3)
    last=calendar_date.fromisoformat(dates[-1])
    latest=snapshots[dates[-1]]
    cases=[{}, {'snapshot_date':dates[(len(dates)-1)//2]},
           {'city':latest['customers'][0]['city'],'category':latest['categories'][0]['name'],
            'date_from':dates[margin],'date_to':dates[-1-margin]},
           {'date_from':(last+timedelta(days=1)).isoformat(),'date_to':(last+timedelta(days=2)).isoformat()}]
    results=[]
    for filters in cases:
        date=filters.get('snapshot_date',max(snapshots))
        wanted=expected(snapshots[date],filters)
        for card,value in zip(artifacts['cards'][:6],wanted):
            r=mb.api('POST',f"card/{card['id']}/query",{'parameters':params(filters)})
            assert r['status']=='completed',r.get('error')
            actual=r['data']['rows'][0][0]
            actual=None if actual is None else Decimal(str(actual))
            assert actual==value,f"{card['name']}, {filters}: {actual} != {value}"
        # Полный топ категорий сверяется с независимой агрегацией JSON.
        cats={c['id']:c['name'] for c in snapshots[date]['categories']}
        products={p['id']:p for p in snapshots[date]['products']}
        customers={c['id']:c for c in snapshots[date]['customers']}
        totals={}
        for order in snapshots[date]['orders']:
            if order['status']!='delivered':continue
            if filters.get('city') and customers[order['customer_id']]['city']!=filters['city']:continue
            if not filters.get('date_from','0001-01-01')<=order['status_updated_at'][:10]<=filters.get('date_to','9999-12-31'):continue
            for item in order['items']:
                category=cats[products[item['product_id']]['category_id']]
                if filters.get('category') and category!=filters['category']:continue
                amount,units=totals.get(category,(Decimal(0),0))
                totals[category]=(amount+Decimal(item['unit_price'])*item['quantity'],units+item['quantity'])
        r=mb.api('POST',f"card/{artifacts['cards'][10]['id']}/query",{'parameters':params(filters)})
        assert r['status']=='completed'
        actual={row[0]:(Decimal(str(row[1])),row[2]) for row in r['data']['rows']}
        assert actual==totals,'Расхождение продаж по категориям'
        results.append({'filters':filters,'metrics':[str(v) if v is not None else None for v in wanted],'passed':True})
    forbidden=mb.api('POST','dataset',{'database':artifacts['database_id'],'type':'native','native':{'query':'SELECT count(*) FROM dds.orders'}},expected_error=True)
    assert forbidden['status']=='failed' and 'permission denied' in forbidden.get('error','').lower(),'BI неожиданно имеет доступ к DDS'
    dashboard=mb.api('GET',f"dashboard/{artifacts['dashboard_id']}")
    assert len(dashboard['dashcards'])==12 and len(dashboard['parameters'])==5
    report={'cases':results,'dds_access_denied':True,'dashboard_cards':12,'dashboard_filters':5}
    (ROOT/'reports/bi-verification.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n', encoding="utf-8")
    print('BI: 28 сверок метрик, пять фильтров и запрет доступа к DDS — успешно')
if __name__=='__main__':main()
