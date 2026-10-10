"""Генератор запускают отдельно; Airflow читает согласованные снимки."""
import os
from datetime import timedelta
import pendulum
from airflow.sdk import dag, task

@dag(dag_id='orders_pipeline', schedule=None, start_date=pendulum.datetime(2026,9,1,tz='Europe/Moscow'), catchup=False, max_active_runs=1, tags=['orders','lab'])
def orders_pipeline():
    @task(retries=1,retry_delay=timedelta(seconds=10))
    def stg():
        from orders_lab.pipeline import load_stg
        load_stg(os.environ['DATASET_MANIFEST'])
    @task(retries=1,retry_delay=timedelta(seconds=10))
    def dds():
        from orders_lab.pipeline import load_dds
        load_dds(os.environ['DATASET_MANIFEST'])
    @task
    def check_dds():
        from orders_lab.pipeline import quality
        return quality('dds')
    @task(retries=1,retry_delay=timedelta(seconds=10))
    def cdm():
        from orders_lab.pipeline import load_cdm
        load_cdm(os.environ['DATASET_MANIFEST'])
    @task
    def check_cdm():
        from orders_lab.pipeline import quality
        return quality('cdm')
    stg() >> dds() >> check_dds() >> cdm() >> check_cdm()
orders_pipeline()
