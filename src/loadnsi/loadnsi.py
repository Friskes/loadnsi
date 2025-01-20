import asyncio
import os
from dataclasses import dataclass
from pathlib import Path

import click
from dotenv import find_dotenv, load_dotenv

from .console_view import log_handler
from .core import OfficialNsiDataHandler, PirateNsiDataHandler
from .file_handlers import NsiFileHandler
from .logger import log, switch_logger
from .model_examplers import ModelExampler
from .web_handlers import OfficialApiNsiWebCrawler, PirateApiNsiWebCrawler

load_dotenv(find_dotenv())


@dataclass(frozen=True)
class LoadnsiConfig:
    NSI_FIXTURES_FOLDER: str
    NSI_PASSPORTS: dict
    DICT_INTERNAL_PK: str
    PASSPORTS_REL: str
    PARENT_DICT_CLS: str
    NSI_DICTIONARIES: dict


def load_config(config_path: str) -> dict:
    """Загружает файл конфигурации как модуль."""
    full_path = os.path.join(os.getcwd(), config_path)
    config_globals = {}
    with open(full_path, encoding='utf-8') as file:
        file_data = file.read()
    exec(file_data, config_globals)
    filtered_keys = {k: v for k, v in config_globals.items() if k in LoadnsiConfig.__annotations__}
    return LoadnsiConfig(**filtered_keys)


config = load_config(os.environ['PATH_TO_LOADNSI_CONFIG'])

fixtures_path = Path(config.NSI_FIXTURES_FOLDER)
if not fixtures_path.exists():
    log.warning("Директория '%s' не существует. Она будет создана.", config.NSI_FIXTURES_FOLDER)
    fixtures_path.mkdir(parents=True, exist_ok=True)

filenames_without_ext = [
    filename.split('.')[0]
    for filename in (config.NSI_PASSPORTS['file'], *config.NSI_DICTIONARIES.keys())
]


@click.command()
@click.option(
    '--log_level',
    '-ll',
    show_default=True,
    default='INFO',
    type=click.Choice(['None', 'DEBUG', 'INFO', 'WARNING'], case_sensitive=False),
    help='Переключить уровень логгирования.',
)
@click.option(
    '--use_official_api',
    show_default=True,
    default=False,
    is_flag=True,
    help='Использовать официальный апи.',
)
@click.option(
    '--remove_files',
    '-rf',
    show_default=True,
    default=False,
    is_flag=True,
    help='Перед запуском удалять все файлы записанные в директории NSI_FIXTURES_FOLDER.',
)
@click.option(
    '--yes',
    '-y',
    is_flag=True,
    help="Пропустить подтверждение для --remove_files (автоматически ответить 'да').",
)
@click.option(
    '--compress_files',
    '-cf',
    show_default=True,
    default='readable',
    type=click.Choice(['readable', 'compact', 'gzip'], case_sensitive=False),
    help='Переключить режим сжатия файлов. '
    'Примечание: Если уже существуют файлы допустим с расширением .gz '
    'и команда вызывается с флагом (readable или compact) '
    'которые в свою очередь создают файлы с расширением .json '
    'то информация о pk внутренней системы будет перенесена в файл с расширением .json '
    'Это позволяет производить безопасный свитч расширений файлов без потери pk',
)
@click.option(
    '--model_examples',
    '-me',
    show_default=True,
    default=None,
    type=click.Choice(['stdout', 'file'], case_sensitive=False),
    help='Выводить в консоль или файл, пример класса модели для каждого справочника, '
    'генерируемый в методах _construct_dict_model_cls и _construct_passport_model_cls',
)
@click.option(
    '--model_examples_params',
    '-mep',
    show_default=True,
    default=None,
    type=click.Choice(['all_fields_not_required'], case_sensitive=False),
    help='Параметры для управления формированием примеров класса модели справочников. '
    'all_fields_not_required - Сделать все поля моделей необязательными: '
    'default=False, blank=True, null=True',
)
@click.option(
    '--do_not_use_nested_data',
    show_default=True,
    default=False,
    is_flag=True,
    help='Использовать поля из первого уровня вложенности справочника '
    'вместо использования вложенного поля data',
)
@click.option(
    '--forced_update',
    '-fu',
    show_default=True,
    default=(),
    type=click.Choice(filenames_without_ext, case_sensitive=False),
    multiple=True,
    help='Названия файлов без расширений записанные через пробелы, '
    'для принудительно обновления, даже если версия справочника не изменилась. '
    '-fu filename1 -fu filename2',
)
def loadnsi(**options):
    """
    #### Логика взаимодействия с командой:

        - Создать конфигурационный файл `loadnsi_config.py` формата::

            NSI_FIXTURES_FOLDER = 'path/to/your/nsi/folder'
            NSI_PASSPORTS = {
                'file': 'Локальное название файла с паспортами справочников',
                'model': 'Локальное название модели с паспортами справочников',
                # Опциональные параметры (fields):
                'fields': <Iterable объект состоящий из полей паспорта (str) которые необходимо оставить в объекте паспорта>,
            }
            DICT_INTERNAL_PK = 'your pk field name *not alias*'
            PASSPORTS_REL = 'your fieldname for ForeignKey to PARENT_DICT_CLS'
            PARENT_DICT_CLS = 'your base cls modelname for dicts'
            NSI_DICTIONARIES = {
                'Локальное название файла справочника 1': {
                    'model': 'Приложение.МодельСправочника1',
                    'oid': 'OID Справочника 1',
                    # Опциональные параметры (filter и fields):
                    'filter': <Callable объект принимает справочник (dict) должен вернуть (bool) оставлять ли этот объект в списке>,
                    'fields': <Iterable объект состоящий из полей справочника (str) которые необходимо оставить в объекте справочника>,
                    'create_sql': <Boolean объект (bool), если True будет создан дублирующий файл справочника в SQL формате>,
                },
                'Локальное название файла справочника 2': {
                    'model': 'Приложение.МодельСправочника2',
                    'oid': 'OID Справочника 2',
                },
            }

        - Для использования официального API НСИ создать в корне проекта файл .env с содержимым::

            NSI_API_USER_KEY=<ключ>

    ---

    #### Поведение команды при разных кейсах:

    - Кейс 1. (локального файла справочника не существует)::

        Скачивается справочник, обрабатываеться и создается файл фикстура с указанным именем
        для последующей прямой установки в указанную модель бд.

    - Кейс 2. (локальный файл справочника уже существует)::

        Сравниваются версии локального и удаленного(последняя) справочников,
        если версии одинаковые то работа с этим файлом пропускается,
        если версии разные то файл перезаписывается актуальными данными
        с сохранением pk ранее созданных записей.
    """  # noqa: E501

    # Ещё есть апи который отдаёт HTML страницу в теле которой лежит
    # <script id="__NEXT_DATA__" type="application/json">
    # в котором содержится вся необходимая passport информация в JSON формате.

    # https://nsi.rosminzdrav.ru/dictionaries/1.2.643.5.1.13.13.11.1522/passport/latest
    # https://nsi.rosminzdrav.ru/dictionaries/1.2.643.5.1.13.13.99.2.197/passport/1.13

    if options['remove_files'] and not options['yes']:
        # Запрашиваем подтверждение
        confirm = click.confirm(
            'Вы уверены, что хотите продолжить с флагом --remove_files=true ?', default=False
        )
        if not confirm:
            click.echo('Запуск отменен.')
            return

    run_loadnsi(**options)


def run_loadnsi(**options):
    if options['log_level'] != 'None':
        switch_logger(
            True,
            level=options['log_level'],
            handler=log_handler,
        )

    nsi_base_url = 'https://nsi.rosminzdrav.ru'
    nsi_files_path = '/api/dataFiles'

    file = NsiFileHandler(
        local_path_prefix=config.NSI_FIXTURES_FOLDER, compress_files=options['compress_files']
    )
    if options['remove_files']:
        file.remove_files()
    nsi_passports, nsi_dicts = file.set_files_extension(
        nsi_passports=config.NSI_PASSPORTS, nsi_dicts=config.NSI_DICTIONARIES
    )

    exampler = ModelExampler(
        file,
        model_examples=options['model_examples'],
        model_examples_params=options['model_examples_params'],
        parent_dict_cls=config.PARENT_DICT_CLS,
    )

    if options['use_official_api']:
        web = OfficialApiNsiWebCrawler(
            nsi_base_url=nsi_base_url,
            nsi_versions_path='/port/rest/versions',
            nsi_passport_path='/port/rest/passport',
            nsi_files_path=nsi_files_path,
            nsi_api_user_key=os.environ.get('NSI_API_USER_KEY', None),
        )
        nsi = OfficialNsiDataHandler(
            web,
            file,
            exampler,
            nsi_passports=nsi_passports,
            nsi_dicts=nsi_dicts,
            do_not_use_nested_data=options['do_not_use_nested_data'],
            forced_update=options['forced_update'],
            dict_internal_pk_field=config.DICT_INTERNAL_PK,
            passports_rel_field=config.PASSPORTS_REL,
        )
    else:
        web = PirateApiNsiWebCrawler(
            nsi_base_url=nsi_base_url,
            nsi_versions_path='/api/versions',
            nsi_passport_path='/_next/data/{buildId}/dictionaries/{identifier}/passport/{version}.json',
            nsi_files_path=nsi_files_path,
        )
        nsi = PirateNsiDataHandler(
            web,
            file,
            exampler,
            nsi_passports=nsi_passports,
            nsi_dicts=nsi_dicts,
            do_not_use_nested_data=options['do_not_use_nested_data'],
            forced_update=options['forced_update'],
            dict_internal_pk_field=config.DICT_INTERNAL_PK,
            passports_rel_field=config.PASSPORTS_REL,
        )

    try:
        asyncio.run(nsi.main())
    except KeyboardInterrupt:
        log.warning('Выполнение было отменено с помощью: CTRL+C')
