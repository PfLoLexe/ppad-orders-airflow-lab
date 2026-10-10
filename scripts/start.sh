#!/usr/bin/env bash
# Полный запуск стенда из чистой папки; Docker должен быть установлен.
set -euo pipefail
cd "$(dirname "$0")/.."
if docker info >/dev/null 2>&1; then
  dc=(docker compose)
elif command -v sudo >/dev/null && sudo -n docker info >/dev/null 2>&1; then
  dc=(sudo -n docker compose)
else
  echo 'Нужен работающий Docker Engine / Docker Desktop и доступ к нему.' >&2
  exit 1
fi
mkdir -p reports
manifest=$(python3 -m orders_lab.generator --catalog data/reference/catalog.json "$@")
python3 scripts/configure.py --manifest "$manifest"
project=$(python3 -c 'from pathlib import Path;v=dict(l.split("=",1) for l in Path(".env").read_text().splitlines() if l and not l.startswith("#"));print(v["COMPOSE_PROJECT_NAME"])')
dc+=(-p "$project")
"${dc[@]}" config --quiet
"${dc[@]}" up -d --wait --wait-timeout 360
# Подождать появления DAG после запуска отдельного процесса разбора.
for attempt in $(seq 1 30); do
  if "${dc[@]}" exec -T airflow airflow dags list -o json 2>/dev/null | python3 -c 'import json,sys;sys.exit(not any(d["dag_id"]=="orders_pipeline" for d in json.load(sys.stdin)))'; then break; fi
  if [ "$attempt" -eq 30 ]; then echo 'Airflow не обнаружил DAG' >&2;exit 1;fi
  sleep 3
done
run_id="setup_$(date -u +%Y%m%dT%H%M%S)"
"${dc[@]}" exec -T airflow airflow dags trigger orders_pipeline --run-id "$run_id"
for attempt in $(seq 1 120); do
  state=$("${dc[@]}" exec -T airflow airflow dags list-runs orders_pipeline -o json 2>/dev/null | python3 -c 'import json,sys;rid=sys.argv[1];print(next((r["state"] for r in json.load(sys.stdin) if r["run_id"]==rid),"queued"))' "$run_id")
  if [ "$state" = success ]; then break; fi
  if [ "$state" = failed ]; then echo 'DAG завершился ошибкой. Проверьте задачи в Airflow.' >&2;exit 1;fi
  if [ "$attempt" -eq 120 ]; then echo 'Истекло время ожидания DAG' >&2;exit 1;fi
  sleep 5
done
python3 scripts/metabase_bootstrap.py
"${dc[@]}" exec -T airflow python /opt/lab/scripts/verify_warehouse.py
python3 scripts/verify_bi.py
echo 'Стенд готов: Airflow http://localhost:8080; Metabase http://localhost:3333. Пароли — в .env.'
