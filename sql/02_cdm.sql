-- Исторический срез на конец указанного дня. Вход — исключительно DDS.
INSERT INTO cdm.order_snapshots
SELECT %(day)s::date,o.order_id,o.created_at,(o.created_at AT TIME ZONE 'Europe/Moscow')::date,
       CASE WHEN s.status_code='delivered' THEN s.valid_from END,
       CASE WHEN s.status_code='delivered' THEN (s.valid_from AT TIME ZONE 'Europe/Moscow')::date END,
       c.customer_id,concat_ws(' ',c.last_name,c.first_name,c.patronymic),ct.city_name,
       s.status_code,st.status_name,sum(i.quantity*i.unit_price),
       CASE WHEN s.status_code='delivered' THEN extract(epoch FROM (s.valid_from-o.created_at))/3600 END
FROM dds.orders o JOIN dds.customers c USING(customer_id) JOIN dds.cities ct USING(city_id)
JOIN dds.order_status_versions s ON s.order_id=o.order_id
 AND s.valid_from < ((%(day)s::date+1)::timestamp AT TIME ZONE 'Europe/Moscow')
 AND (s.valid_to IS NULL OR s.valid_to >= ((%(day)s::date+1)::timestamp AT TIME ZONE 'Europe/Moscow'))
JOIN dds.statuses st ON st.status_code=s.status_code JOIN dds.order_items i ON i.order_id=o.order_id
WHERE o.created_at < ((%(day)s::date+1)::timestamp AT TIME ZONE 'Europe/Moscow')
GROUP BY o.order_id,c.customer_id,ct.city_name,s.status_code,st.status_name,s.valid_from;

INSERT INTO cdm.order_item_snapshots
SELECT %(day)s::date,i.order_id,i.line_no,p.product_id,p.product_name,p.category_id,c.category_name,
       i.quantity,i.unit_price,i.quantity*i.unit_price
FROM dds.order_items i JOIN dds.product_versions p USING(product_version_id)
JOIN dds.categories c USING(category_id) JOIN dds.orders o USING(order_id)
WHERE o.created_at < ((%(day)s::date+1)::timestamp AT TIME ZONE 'Europe/Moscow');

INSERT INTO cdm.daily_orders
SELECT snapshot_date,created_date,city_name,count(*),sum(order_amount),count(*) FILTER(WHERE status_code='cancelled')
FROM cdm.order_snapshots WHERE snapshot_date=%(day)s::date GROUP BY snapshot_date,created_date,city_name;

INSERT INTO cdm.daily_sales
SELECT o.snapshot_date,o.delivered_date,o.city_name,i.category_id,i.category_name,sum(i.quantity),sum(i.line_amount)
FROM cdm.order_snapshots o JOIN cdm.order_item_snapshots i USING(snapshot_date,order_id)
WHERE o.snapshot_date=%(day)s::date AND o.status_code='delivered'
GROUP BY o.snapshot_date,o.delivered_date,o.city_name,i.category_id,i.category_name;
