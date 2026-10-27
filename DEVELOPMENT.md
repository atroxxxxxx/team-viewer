# Работа с ticket-service

Нужен Python 3.11+ с поддержкой `venv`. Среда разработки устанавливается в `.venv`, версии фиксируются в `requirements-dev.lock`. Команды выполняются из корня репозитория: `dev.cmd` в Windows, `sh dev.sh` в Linux. Активация окружения и системный pip не требуются.

## Первоначальная настройка Linux

Для Debian/Ubuntu с Python 3.11+:

```sh
sudo apt update
sudo apt install python3 python3-venv postgresql
sh dev.sh setup --postgres
sh dev.sh db-start
sh dev.sh test -q
sh dev.sh serve --fake-relay
```

Для Fedora установите зависимости через `sudo dnf install python3 python3-pip postgresql-server`, затем выполните те же команды `sh dev.sh ...`. На других дистрибутивах нужны Python 3.11+, модуль `venv`/`ensurepip` и серверные бинарники PostgreSQL; рекомендуемая версия PostgreSQL — 16, как в compose. Для отдельного Python можно задать `SUPPORT_PYTHON=python3.12 sh dev.sh setup --postgres`.

В Linux `setup --postgres` находит установленный PostgreSQL через `PG_BIN`, PATH, `pg_config --bindir`, каталоги Debian `/usr/lib/postgresql/<версия>/bin` и RPM `/usr/pgsql-<версия>/bin`. ZIP для Windows не скачивается. При необходимости задайте `PG_BIN=/путь/к/postgresql/bin`; в каталоге должны находиться `pg_ctl`, `initdb` и `postgres`.

`sudo` нужен только для установки системных пакетов. Команды проекта выполняются обычным пользователем: `initdb` создаёт отдельный кластер в `.dev/pgdata`, системная служба PostgreSQL не используется. Кластер слушает TCP `127.0.0.1:15432`, системный Unix-сокет не создаётся. Новый `.env.dev` и временный файл пароля доступны только владельцу. Остановка: `sh dev.sh db-stop`.

Используйте отдельный клон для Windows и Linux/WSL: `.venv` и каталог данных PostgreSQL нельзя переносить между ОС.

## Первоначальная настройка Windows

```bat
py -3 scripts\dev.py setup --postgres
dev.cmd db-start
dev.cmd test -q
```

`setup` устанавливает зависимости ticket-service и pytest в `.venv`, создаёт `.env.dev` со случайными локальными паролями и сохраняет существующий файл при повторном запуске. В Windows ключ `--postgres` скачивает [ZIP PostgreSQL 16 от EDB](https://www.enterprisedb.com/download-postgresql-binaries) в `.dev`; системная служба, PATH и настройки установленной БД не меняются. Можно указать свой каталог бинарников через `PG_BIN`.

`db-start` запускает только локальный кластер `.dev/pgdata` на `127.0.0.1:15432`, создаёт базы `support_dev` и `support_test` и применяет исходную SQL-схему к новой dev-базе. Повторный запуск не удаляет данные и не выполняет init второй раз. Для остановки: `dev.cmd db-stop`. Секреты, данные PostgreSQL, архивы и виртуальная среда исключены из Git.

## Ежедневная работа

```bat
dev.cmd db-start
dev.cmd test -q
dev.cmd check
dev.cmd serve --fake-relay
```

В Linux: `sh dev.sh db-start`, `sh dev.sh test -q`, `sh dev.sh check`, `sh dev.sh serve --fake-relay`.

API открывается на `http://127.0.0.1:18080`, Swagger — `/swagger/index.html`, проверка БД — `/health`. Basic-пароли находятся в локальном `.env.dev`.

`--fake-relay` включает явную заглушку только для разработки ticket API: можно пройти создание, claim, разрешение, план и закрытие; экран и камера не передаются. Для настоящего ретранслятора запустите `dev.cmd serve`, задав его адрес и токен в `.env.dev`. Полный Linux-стек и Go-ретранслятор по-прежнему запускаются существующим compose; перед его сборкой требуется `go mod vendor`.

## Тесты и другая PostgreSQL

HTTP-регрессии и конкурентные операции проверяются на настоящем PostgreSQL с исходной секционированной схемой, а сетевой relay в тестах заменён заглушкой. Каждый тестовый запуск создаёт уникальную схему `ticket_test_<uuid>` и удаляет только её после завершения. Роль подключения должна иметь право CREATE SCHEMA; указывайте отдельную тестовую базу через `TEST_DATABASE_URL`. `DATABASE_URL` предназначен для обычного запуска сервиса.

```sh
python -m pip install -r requirements-dev.txt
TEST_DATABASE_URL=postgresql+psycopg://user:password@127.0.0.1:5432/support_test python -m pytest -q
python -m pytest -m 'not integration' -q
```

Без `TEST_DATABASE_URL` и `.env.dev` интеграционные тесты пропускаются с пояснением. Обычный `dev.cmd test` подхватывает `.env.dev` и запускает их вместе с прежними тестами общих правил, media-helper и моделей оператора. Проверки GUI, RTSP и настоящего Redis требуют полного стека.

## Изменённое поведение API

- `allow` и `accept` обязательны и принимают только JSON `true`/`false`; `camera` и `seen_on_screen` тоже требуют boolean, при отсутствии равны false. Неверный JSON или JSON не в форме объекта возвращает 400.
- Повторный/конкурентный claim занятой заявки получает 409 до обращения к relay. Строка заявки блокируется до завершения открытия и фиксации назначения. Сетевой вызов удерживает блокировку максимум в пределах таймаута relay; восстановления после падения процесса между HTTP и commit пока нет.
- После `resolved` изменения заявки возвращают 409; все active/pending-гранты отозваны, ожидающий ввод отменён в той же транзакции. Снимок закрытой заявки остаётся доступен для чтения. Отрицательное подтверждение при одном pending-гранте возвращает guided.

Существующая попытка удаления туннеля после commit остаётся без долговечного повтора. Эти изменения не добавляют worker/outbox и не обещают разрыв уже открытого media-соединения на стороне relay.
