# Сборка deb для клиента и оператора

Серверная сборка курсовика — `docker compose` из [отчёта](README.md#sborka). Два настольных приложения пакуются как пакеты Debian и ставятся на Linux-системы семейства Debian: Debian, Ubuntu, Kali. Сервис заявок и ретранслятор в эти пакеты не входят, они запускаются своими `docker-compose.yaml`.

Получаются три бинарных пакета из одного исходного:

| Пакет | Что ставит |
| --- | --- |
| `python3-support-protocol` | Общий валидатор и HTTP-клиент заявок |
| `support-client` | Окно клиента, пункт меню, `/usr/bin/support-client` |
| `support-operator` | Окно оператора, пункт меню, `/usr/bin/support-operator` |

Модели в пакет не кладутся. Первый запуск `support-operator` скачивает их с Hugging Face в `$HOME/.local/share/support-operator/models`, размер зависит от памяти машины. `support-operator --no-models` ничего не скачивает и оставляет оператора человеком.

## Зависимости

На машине сборки:

```bash
sudo apt install devscripts debhelper dh-python pybuild-plugin-pyproject python3-all python3-setuptools
```

На машине пользователя пакеты сами потянут:

- `python3-pyqt5`, `python3-httpx`, `python3-yaml` — оба окна;
- `ffmpeg` — экран;
- `xdotool` — ввод на клиенте;
- `python3-huggingface-hub`, `python3-pil` — скачивание и кадр оператора.

Зрительный и текстовый запуск идёт через `llama-cpp-python`. В Debian этого модуля часто нет. Перед сборкой проверьте:

```bash
apt-cache search llama-cpp
apt-cache search pyqt5
```

Если модуля нет, операторский пакет оставляет его в `Recommends`, а окно при шаге модели пишет, что нужен `requirements-models.txt`. Поставить его в то же окружение, которым пользуется пакет:

```bash
sudo apt install python3-pip
pip3 install --user -r operator-app/requirements-models.txt
```

Перед сборкой проверьте наличие `python3-pyqt5` в репозиториях целевого выпуска Debian: `apt-cache show python3-pyqt5`.

## Исходники

Рядом с корнем репозитория создайте каталог `debian`. Файлы ниже копируются как есть.

`debian/control`:

```text
Source: remote-support-desktop
Section: net
Priority: optional
Maintainer: Support <support@localhost>
Build-Depends: debhelper-compat (= 13), dh-python, pybuild-plugin-pyproject, python3-all, python3-setuptools
Standards-Version: 4.7.0
Rules-Requires-Root: no

Package: python3-support-protocol
Architecture: all
Depends: ${python3:Depends}, python3-httpx, python3-yaml
Description: общий протокол техподдержки
 Валидатор ввода и клиент сервиса заявок для окон клиента и оператора.

Package: support-client
Architecture: all
Depends: ${python3:Depends}, ${misc:Depends}, python3-support-protocol, python3-pyqt5, python3-httpx, python3-yaml, ffmpeg, xdotool
Description: клиент удалённой техподдержки
 Окно, в котором клиент создаёт заявку и исходящим подключением отдаёт экран.

Package: support-operator
Architecture: all
Depends: ${python3:Depends}, ${misc:Depends}, python3-support-protocol, python3-pyqt5, python3-httpx, python3-yaml, python3-huggingface-hub, python3-pil, ffmpeg
Recommends: python3-llama-cpp
Description: оператор удалённой техподдержки
 Окно оператора. Человек работает сразу. Связка локальных моделей скачивается при первом запуске.
```

`debian/rules`:

```text
#!/usr/bin/make -f

export PYBUILD_NAME=remote-support-desktop
export PYBUILD_DISABLE=test

%:
	dh $@ --with python3 --buildsystem=pybuild

override_dh_auto_install:
	dh_auto_install
	mkdir -p debian/support-client/usr/bin debian/support-operator/usr/bin
	mkdir -p debian/support-client/usr/share/applications debian/support-operator/usr/share/applications
	cp debian/support-client.wrapper debian/support-client/usr/bin/support-client
	cp debian/support-operator.wrapper debian/support-operator/usr/bin/support-operator
	chmod 0755 debian/support-client/usr/bin/support-client debian/support-operator/usr/bin/support-operator
	cp debian/support-client.desktop debian/support-client/usr/share/applications/support-client.desktop
	cp debian/support-operator.desktop debian/support-operator/usr/share/applications/support-operator.desktop
	mkdir -p debian/support-client/etc/support-client debian/support-operator/etc/support-operator
	cp client-app/config.yaml debian/support-client/etc/support-client/config.yaml
	cp operator-app/config.yaml debian/support-operator/etc/support-operator/config.yaml
```

`pybuild` собирает пакеты из корневого `pyproject.toml`. Он уже лежит в репозитории и только перечисляет каталоги, модели в пакет не входят:

```text
[build-system]
requires = ["setuptools>=61"]
build-backend = "setuptools.build_meta"

[project]
name = "remote-support-desktop"
version = "0.1.0"
description = "Клиент и оператор удалённой техподдержки"
requires-python = ">=3.11"

[tool.setuptools]
packages = ["support_protocol", "client_app", "operator_app"]

[tool.setuptools.package-dir]
support_protocol = "support-protocol/support_protocol"
client_app = "client-app/client_app"
operator_app = "operator-app/operator_app"
```

`debian/support-client.wrapper`:

```text
#!/bin/sh
export SUPPORT_CLIENT_CONFIG="${SUPPORT_CLIENT_CONFIG:-/etc/support-client/config.yaml}"
exec python3 -m client_app.main
```

`debian/support-operator.wrapper`:

```text
#!/bin/sh
export SUPPORT_OPERATOR_CONFIG="${SUPPORT_OPERATOR_CONFIG:-/etc/support-operator/config.yaml}"
export SUPPORT_MODELS_DIR="${SUPPORT_MODELS_DIR:-$HOME/.local/share/support-operator/models}"
exec python3 -m operator_app.main "$@"
```

Окна читают `config.yaml` рядом с кодом. Если задана `SUPPORT_CLIENT_CONFIG` или `SUPPORT_OPERATOR_CONFIG`, берётся этот файл. Обёртки пакета указывают на `/etc`, поэтому правку адреса после установки не нужно зашивать обратно в пакет.

`debian/support-client.desktop`:

```text
[Desktop Entry]
Type=Application
Name=Техподдержка: клиент
Comment=Заявка и экран для оператора
Exec=support-client
Terminal=false
Categories=Network;
```

`debian/support-operator.desktop`:

```text
[Desktop Entry]
Type=Application
Name=Техподдержка: оператор
Comment=Очередь заявок и локальные модели
Exec=support-operator
Terminal=false
Categories=Network;
```

Ещё три коротких файла:

`debian/changelog` — одна запись `remote-support-desktop (0.1.0-1) unstable; urgency=medium`.

`debian/source/format` — строка `3.0 (native)`.

`debian/python3-support-protocol.install`, `debian/support-client.install` и `debian/support-operator.install` можно не заводить, если `pybuild` уже кладёт модули в общий `dist-packages`, а `debian/rules` раскладывает программы по пакетам через каталоги `debian/<пакет>/`. Если `dh_install` ругается, что файлы попали не в тот пакет, добавьте в каждый `*.install` строки вида:

```text
usr/lib/python3*/dist-packages/client_app/*
usr/bin/support-client
usr/share/applications/support-client.desktop
etc/support-client/config.yaml
```

и то же для `operator_app` и `support_protocol`.

## Сборка и установка

Из корня репозитория, рядом с каталогом `debian`:

```bash
dpkg-buildpackage -us -uc -b
sudo apt install ../python3-support-protocol_0.1.0-1_all.deb ../support-client_0.1.0-1_all.deb ../support-operator_0.1.0-1_all.deb
```

Проверка:

```bash
support-client
support-operator --no-models
support-operator
```

Первая команда оператора открывает окно человека и не ходит в сеть за весами. Вторая, без флага, при отсутствии файлов скачивает три модели под текущую память. Каталог весов можно перенести на другой диск переменной `SUPPORT_MODELS_DIR`.

Конфиги после установки: `/etc/support-client/config.yaml` и `/etc/support-operator/config.yaml`. В них адрес сервиса заявок и сессионный порт ретранслятора. Пароли прототипа замените до раздачи пакета.
