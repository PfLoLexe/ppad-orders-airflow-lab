"""Создаёт учебный дашборд; повторы обновляют существующие объекты."""
import json
import os
from pathlib import Path
from urllib.request import Request,urlopen
from urllib.error import HTTPError

ROOT=Path(__file__).resolve().parents[1]
ENV=dict(line.split('=',1) for line in (ROOT/'.env').read_text(encoding="utf-8").splitlines() if line and not line.startswith('#'))
BASE=os.environ.get('METABASE_URL','http://127.0.0.1:3333')
SESSION=None

def api(method,path,body=None,expected_error=False):
    headers={'Content-Type':'application/json'}
    if SESSION: headers['X-Metabase-Session']=SESSION
    req=Request(BASE+'/api/'+path,data=None if body is None else json.dumps(body).encode(),headers=headers,method=method)
    try:
        with urlopen(req,timeout=120) as r: return json.load(r)
    except HTTPError as e:
        raw=e.read().decode()
        if expected_error and e.code==400:
            return json.loads(raw)
        # Не печатать request/credentials.
        raise RuntimeError(f'Metabase {method} {path}: HTTP {e.code}; {raw[:1500]}') from None

PARAMS=[('snapshot_date','Дата среза','date','date/single'),('date_from','Период: с','date','date/single'),('date_to','Период: по','date','date/single'),('city','Город','text','string/='),('category','Категория','text','string/=')]
TAGS={key:dict(id=key,name=key,**{'display-name':label,'type':typ,'required':False}) for key,label,typ,_ in PARAMS}
CTE="""WITH selected_items AS (
 SELECT * FROM cdm.order_item_snapshots
 WHERE snapshot_date=COALESCE([[{{snapshot_date}}::date,]] (SELECT max(snapshot_date) FROM cdm.order_snapshots))
 [[AND category_name={{category}}]]
), selected AS (
 SELECT o.*,x.selected_amount FROM cdm.order_snapshots o
 JOIN (SELECT snapshot_date,order_id,sum(line_amount) selected_amount FROM selected_items GROUP BY snapshot_date,order_id) x USING(snapshot_date,order_id)
 WHERE o.snapshot_date=COALESCE([[{{snapshot_date}}::date,]] (SELECT max(snapshot_date) FROM cdm.order_snapshots))
 [[AND o.city_name={{city}}]]
)
"""
CREATED="FROM selected WHERE true [[AND created_date>={{date_from}}]] [[AND created_date<={{date_to}}]]"
DELIVERED="FROM selected WHERE status_code='delivered' [[AND delivered_date>={{date_from}}]] [[AND delivered_date<={{date_to}}]]"
ITEMS="FROM selected o JOIN selected_items i USING(snapshot_date,order_id) WHERE o.status_code='delivered' [[AND o.delivered_date>={{date_from}}]] [[AND o.delivered_date<={{date_to}}]]"
CARDS=[
 ('Количество заказов','scalar','SELECT count(*) AS "Заказы" '+CREATED,{}),
 ('Оформленная сумма, ₽','scalar','SELECT coalesce(sum(selected_amount),0) AS "Рубли" '+CREATED,{}),
 ('Доставленные продажи, ₽','scalar','SELECT coalesce(sum(selected_amount),0) AS "Рубли" '+DELIVERED,{}),
 ('Средний доставленный чек, ₽','scalar','SELECT round(avg(selected_amount),2) AS "Рубли" '+DELIVERED,{}),
 ('Доля отмен, %','scalar',"SELECT round(100.0*count(*) FILTER(WHERE status_code='cancelled')/nullif(count(*),0),2) AS \"Проценты\" "+CREATED,{}),
 ('Средний срок доставки, ч','scalar','SELECT round(avg(delivery_hours),2) AS "Часы" '+DELIVERED,{}),
 ('Оформленные заказы по дням','line','SELECT created_date AS "Дата",count(*) AS "Заказы" '+CREATED+' GROUP BY created_date ORDER BY created_date',{'graph.dimensions':['Дата'],'graph.metrics':['Заказы']}),
 ('Доставленные продажи по дням','line','SELECT delivered_date AS "Дата",sum(selected_amount) AS "Рубли" '+DELIVERED+' GROUP BY delivered_date ORDER BY delivered_date',{'graph.dimensions':['Дата'],'graph.metrics':['Рубли']}),
 ('Состояние заказов','pie','SELECT status_name AS "Статус",count(*) AS "Заказы" '+CREATED+' GROUP BY status_name',{'pie.dimension':'Статус','pie.metric':'Заказы'}),
 ('Топ товаров по продажам','bar','SELECT i.product_name AS "Товар",sum(i.line_amount) AS "Рубли",sum(i.quantity) AS "Штуки" '+ITEMS+' GROUP BY i.product_id,i.product_name ORDER BY sum(i.line_amount) DESC LIMIT 10',{'graph.dimensions':['Товар'],'graph.metrics':['Рубли'],'graph.x_axis.labels_enabled':False}),
 ('Продажи по категориям','bar','SELECT i.category_name AS "Категория",sum(i.line_amount) AS "Рубли",sum(i.quantity) AS "Штуки" '+ITEMS+' GROUP BY i.category_name ORDER BY sum(i.line_amount) DESC',{'graph.dimensions':['Категория'],'graph.metrics':['Рубли']}),
 ('Заказы: детализация','table','SELECT order_id AS "Заказ",created_date AS "Оформлен",customer_full_name AS "ФИО",city_name AS "Город",status_name AS "Статус",selected_amount AS "Сумма, ₽",delivered_date AS "Доставлен" '+CREATED+' ORDER BY order_id',{}),
]

def main():
    global SESSION
    props=api('GET','session/properties')
    if props.get('setup-token'):
        SESSION=api('POST','setup',{'token':props['setup-token'],'user':{'first_name':'Администратор','last_name':'Лабораторной','email':'admin@orders-lab.invalid','password':ENV['METABASE_ADMIN_PASSWORD']},'prefs':{'site_name':'Учебный магазин — заказы','site_locale':'ru'}})['id']
    else:
        SESSION=api('POST','session',{'username':'admin@orders-lab.invalid','password':ENV['METABASE_ADMIN_PASSWORD']})['id']
    databases=api('GET','database')
    if isinstance(databases,dict): databases=databases['data']
    database=next((d for d in databases if d['name']=='Заказы — CDM'),None)
    if not database:
        database=api('POST','database',{'name':'Заказы — CDM','engine':'postgres','details':{'host':'postgres','port':5432,'dbname':'warehouse','user':'bi_reader','password':ENV['BI_PASSWORD'],'ssl':False,'schema-filters-type':'inclusion','schema-filters-patterns':'cdm'},'is_full_sync':True})
    collections=api('GET','collection')
    collection=next((c for c in collections if c['name']=='Лабораторная — заказы'),None)
    if not collection: collection=api('POST','collection',{'name':'Лабораторная — заказы','description':'Синтетические данные. Рубли. Исторический срез на конец выбранного дня.'})
    contents=api('GET',f"collection/{collection['id']}/items")['data']
    dashboard=next((c for c in contents if c['name']=='Заказы интернет-магазина' and c['model']=='dashboard'),None)
    if not dashboard:
        dashboard=api('POST','dashboard',{'name':'Заказы интернет-магазина','collection_id':collection['id'],'description':'По умолчанию последний срез. Период относится к оформлению для заказов/отмен и к доставке для продаж/чека/срока/топов. Категория ограничивает позиции и заказы, содержащие их; чек — сумма выбранных позиций. Учебные данные и цены.'})
    current=api('GET',f"dashboard/{dashboard['id']}")
    slots={c['card_id']:c['id'] for c in current['dashcards'] if c.get('card_id')}
    dashcards=[]; card_info=[]
    for index,(name,display,sql,settings) in enumerate(CARDS):
        found=next((c for c in contents if c['model']=='card' and c['name']==name),None)
        payload={'name':name,'description':'Источник: CDM. Пустой средний показатель означает отсутствие доставленных заказов.','display':display,'collection_id':collection['id'],'visualization_settings':settings,'dataset_query':{'database':database['id'],'type':'native','native':{'query':CTE+sql,'template-tags':TAGS}}}
        card=api('PUT' if found else 'POST',f"card/{found['id']}" if found else 'card',payload)
        card_info.append({'id':card['id'],'name':name})
        if index<6: row=(index//3)*4;col=(index%3)*8;w=8;h=4
        elif index<8: row=8;col=(index-6)*12;w=12;h=7
        elif index<10: row=15;col=(index-8)*12;w=12;h=8
        elif index==10: row=23;col=0;w=24;h=7
        else: row=30;col=0;w=24;h=10
        dashcards.append({'id':slots.get(card['id'],-index-1),'card_id':card['id'],'row':row,'col':col,'size_x':w,'size_y':h,'parameter_mappings':[{'parameter_id':key,'card_id':card['id'],'target':['variable',['template-tag',key]]} for key,_,_,_ in PARAMS]})
    parameters=[{'id':key,'name':label,'slug':key,'type':ptype} for key,label,_,ptype in PARAMS]
    api('PUT',f"dashboard/{dashboard['id']}",{'dashcards':dashcards,'parameters':parameters,'tabs':[]})
    report={'database_id':database['id'],'collection_id':collection['id'],'dashboard_id':dashboard['id'],'dashboard_path':f"/dashboard/{dashboard['id']}",'cards':card_info}
    (ROOT/'reports/metabase-artifacts.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n', encoding="utf-8")
    # Выполнить каждый вопрос с фильтрами по умолчанию, не раскрывая сессию.
    results=[]
    for c in card_info:
        data=api('POST',f"card/{c['id']}/query",{'parameters':[]})
        if data.get('status')!='completed': raise RuntimeError(f"Вопрос {c['name']}: {data.get('error')}")
        results.append({'name':c['name'],'rows':data['data']['rows'][:10],'row_count':data.get('row_count')})
    (ROOT/'reports/metabase-results.json').write_text(json.dumps(results,ensure_ascii=False,indent=2)+'\n', encoding="utf-8")
    print(f"Дашборд {dashboard['id']}: {len(card_info)} вопросов, все запросы выполнены")

if __name__=='__main__': main()
