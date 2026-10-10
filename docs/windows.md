# Windows: локальный запуск

## Локальный стенд

Нужны Git, Python 3.9 или новее в PATH и Docker Desktop в режиме Linux containers. Выделите Docker около 5 ГиБ памяти. В PowerShell:

```powershell
git clone https://github.com/PfLoLexe/ppad-orders-airflow-lab.git
cd ppad-orders-airflow-lab
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\start.ps1
```

`ExecutionPolicy Bypass` действует для запускаемого процесса. Скрипт создаёт данные и индивидуальные пароли, запускает три контейнера, DAG и настройку Metabase, затем проверки. Первый запуск скачивает образы из интернета.

Локальные адреса: Airflow http://localhost:8080, Metabase http://localhost:3333, PostgreSQL `localhost:15432`. Пароли — в `.env`. Airflow: пользователь `admin`; Metabase: `admin@orders-lab.invalid`. Для изменения объёма используйте [инструкцию по параметрам](scaling.md).

Скрипт PowerShell подготовлен; выполнение на Windows отдельно не проверялось. Полный Linux-запуск проверен на Ubuntu. Команды Docker в README подходят для PowerShell; вместо `python3` используйте `python`, если так называется ваш Python.

## Архив исходников

Репозиторий можно получить через Git или кнопку **Code → Download ZIP** на GitHub. Отдельный архив стенда создаётся командой `python scripts/package.py` рядом с папкой проекта. Архив включает исходники, снимки и отчёты; `.env`, ключи, служебные тома БД и локальные журналы в него не входят.
