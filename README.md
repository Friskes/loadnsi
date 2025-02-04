# Утилита для загрузки и обновления фикстур НСИ

<div align="center">

| Project   |     | Status                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                        |
|-----------|:----|-------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| CI/CD     |     | [![Latest Release](https://github.com/Friskes/loadnsi/actions/workflows/publish-to-pypi.yml/badge.svg)](https://github.com/Friskes/loadnsi/actions/workflows/publish-to-pypi.yml)                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                             |
| Quality   |     | [![Coverage](https://codecov.io/github/Friskes/loadnsi/graph/badge.svg?token=vKez4Pycrc)](https://codecov.io/github/Friskes/loadnsi)                                                                                                                                                                                                                                                                                                                               |
| Package   |     | [![PyPI - Version](https://img.shields.io/pypi/v/loadnsi?labelColor=202235&color=edb641&logo=python&logoColor=edb641)](https://badge.fury.io/py/loadnsi) ![PyPI - Support Python Versions](https://img.shields.io/pypi/pyversions/loadnsi?labelColor=202235&color=edb641&logo=python&logoColor=edb641) ![Project PyPI - Downloads](https://img.shields.io/pypi/dm/loadnsi?logo=python&label=downloads&labelColor=202235&color=edb641&logoColor=edb641)                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                  |
| Meta      |     | [![types - Mypy](https://img.shields.io/badge/types-Mypy-202235.svg?logo=python&labelColor=202235&color=edb641&logoColor=edb641)](https://github.com/python/mypy) [![License - MIT](https://img.shields.io/badge/license-MIT-202235.svg?logo=python&labelColor=202235&color=edb641&logoColor=edb641)](https://spdx.org/licenses/) [![code style - Ruff](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ruff/main/assets/badge/format.json&labelColor=202235)](https://github.com/astral-sh/ruff) |

</div>

> Команда [loadnsi](#About-command) позволяет загружать и обновлять фикстуры [НСИ](https://nsi.rosminzdrav.ru) в которых записи имеют стабильный pk вашей внутренней системы бд.


## Подготовка к запуску
1. Установка пакета
    ```bash
    pip install loadnsi
    ```

2. Создание файла переменных среды
    .env файл с содержимым:
    ```bash
    # Опционально, позволяет запускать команду из вложенных директорий проекта.
    ABS_ROOT_DIR=C:/path/to/your/project/dir
    PATH_TO_LOADNSI_CONFIG=path/to/your/config/file.py
    # Опционально, требуется только если вы собираетесь использовать официальный API НСИ (Запуск команды с флагом `--use_official_api`).
    NSI_API_USER_KEY=some-key
    ```

3. Создание файла конфигурации `loadnsi_config.py`
    ```python
    NSI_FIXTURES_FOLDER = 'path/to/your/nsi/folder'
    NSI_PASSPORTS = {
        'file': 'Локальное название файла с паспортами справочников',
        'model': 'Локальное название модели с паспортами справочников',
        # Опциональные параметры (include, exclude):
        'include': <Iterable объект состоящий из полей паспорта (str) которые необходимо оставить в объекте паспорта>,
        'exclude': <Iterable объект состоящий из полей паспорта (str) которые необходимо исключить из объекта паспорта>,
    }
    DICT_INTERNAL_PK = 'your pk field name *not alias*'
    PASSPORTS_REL = 'your fieldname for ForeignKey to PARENT_DICT_CLS'
    PARENT_DICT_CLS = 'your base cls modelname for dicts'
    NSI_DICTIONARIES = {
        'Локальное название файла справочника 1': {
            'model': 'Приложение.МодельСправочника1',
            'oid': 'OID Справочника 1',
            # Опциональные параметры (filter, include, exclude и create_sql):
            'filter': <Callable объект принимает справочник (dict) должен вернуть (bool) оставлять ли этот объект в списке>,
            'include': <Iterable объект состоящий из полей справочника (str) которые необходимо оставить в объекте справочника>,
            'exclude': <Iterable объект состоящий из полей справочника (str) которые необходимо исключить из объекта справочника>,
            'create_sql': <Boolean объект (bool), если True будет создан дублирующий файл справочника в SQL формате>,
        },
        'Локальное название файла справочника 2': {
            'model': 'Приложение.МодельСправочника2',
            'oid': 'OID Справочника 2',
        },
    }
    ```


## About command
Подробности про каждую опцию команды можно узнать вызвав команду с флагом `--help`


## Contributing
We would love you to contribute to `loadnsi`, pull requests are very welcome! Please see [CONTRIBUTING.md](https://github.com/Friskes/loadnsi/blob/main/CONTRIBUTING.md) for more information.
