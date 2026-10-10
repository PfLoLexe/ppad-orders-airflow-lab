# Полный запуск в PowerShell. Нужны Docker Desktop (Linux containers) и Python 3.9+.
param(
    [int]$Orders = 1000,
    [int]$Customers = 100,
    [int]$Days = 30,
    [int]$Products = 30,
    [int]$Seed = 42,
    [string]$StartDate = '2026-09-01'
)
$ErrorActionPreference = 'Stop'
Push-Location (Join-Path $PSScriptRoot '..')
try {
    & docker info *> $null
    if ($LASTEXITCODE -ne 0) { throw 'Запустите Docker Desktop в режиме Linux containers.' }
    if (Get-Command python -ErrorAction SilentlyContinue) { $py = 'python' }
    elseif (Get-Command python3 -ErrorAction SilentlyContinue) { $py = 'python3' }
    else { throw 'Установите Python 3.9 или новее и добавьте его в PATH.' }
    New-Item -ItemType Directory -Force reports | Out-Null
    $manifest = & $py -m orders_lab.generator --catalog data/reference/catalog.json --orders $Orders --customers $Customers --days $Days --products $Products --seed $Seed --start-date $StartDate
    if ($LASTEXITCODE -ne 0) { throw 'Ошибка генерации данных' }
    & $py scripts/configure.py --manifest $manifest
    if ($LASTEXITCODE -ne 0) { throw 'Ошибка настройки' }
    & docker compose config --quiet
    if ($LASTEXITCODE -ne 0) { throw 'Ошибка Compose' }
    & docker compose up -d --wait --wait-timeout 360
    if ($LASTEXITCODE -ne 0) { throw 'Контейнеры не готовы; проверьте docker compose logs' }
    $found = $false
    for ($n = 0; $n -lt 30; $n++) {
        $raw = & docker compose exec -T airflow airflow dags list -o json
        if ($LASTEXITCODE -eq 0) {
            $dags = ($raw | Out-String | ConvertFrom-Json)
            if ($dags | Where-Object { $_.dag_id -eq 'orders_pipeline' }) { $found = $true; break }
        }
        Start-Sleep -Seconds 3
    }
    if (-not $found) { throw 'Airflow не обнаружил DAG' }
    $runId = 'setup_' + [DateTime]::UtcNow.ToString('yyyyMMddTHHmmss')
    & docker compose exec -T airflow airflow dags trigger orders_pipeline --run-id $runId
    if ($LASTEXITCODE -ne 0) { throw 'Не удалось запустить DAG' }
    $success = $false
    for ($n = 0; $n -lt 120; $n++) {
        $raw = & docker compose exec -T airflow airflow dags list-runs orders_pipeline -o json
        if ($LASTEXITCODE -ne 0) { throw 'Не удалось прочитать состояние DAG' }
        $runs = ($raw | Out-String | ConvertFrom-Json)
        $run = $runs | Where-Object { $_.run_id -eq $runId } | Select-Object -First 1
        if ($run.state -eq 'success') { $success = $true; break }
        if ($run.state -eq 'failed') { throw 'DAG завершился ошибкой; откройте Airflow' }
        Start-Sleep -Seconds 5
    }
    if (-not $success) { throw 'Истекло время ожидания DAG' }
    & $py scripts/metabase_bootstrap.py
    if ($LASTEXITCODE -ne 0) { throw 'Не удалось создать дашборд' }
    & docker compose exec -T airflow python /opt/lab/scripts/verify_warehouse.py
    if ($LASTEXITCODE -ne 0) { throw 'Проверка warehouse не прошла' }
    & $py scripts/verify_bi.py
    if ($LASTEXITCODE -ne 0) { throw 'Проверка BI не прошла' }
    Write-Host 'Готово: Airflow http://localhost:8080; Metabase http://localhost:3333. Пароли: .env'
}
finally { Pop-Location }
