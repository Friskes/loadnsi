import abc
import asyncio
import logging
import time
import uuid
from collections.abc import Callable, Iterable
from typing import Any

from .console_view import rich_live
from .dtos import DictState
from .exceptions import BadSubclassError, NsiPkNotFoundError
from .file_handlers import FileHandler
from .logger import log
from .model_examplers import Exampler
from .web_handlers import NsiWebCrawler


# TODO(Ars): Разделить логику паспортов и справочников на 2 разных класса
class NsiDataHandler(abc.ABC):
    """Базовый класс для определения логики относящейся к обработке данных."""

    # NOTE(Ars): Поля необходимые для работы программы
    PASSPORT_RESERVED_FIELDS = {'oid', 'version'}

    def __init__(
        self,
        web: NsiWebCrawler,
        file: FileHandler,
        exampler: Exampler,
        nsi_passports: dict[str, str],
        nsi_dicts: dict[str, dict],
        do_not_use_nested_data: bool,
        forced_update: tuple[str, ...],
        dict_internal_pk_field: str,
        passports_rel_field: str,
    ) -> None:
        if not issubclass(type(web), NsiWebCrawler):
            raise BadSubclassError('The web handler must be a subclass of NsiWebCrawler')

        if not issubclass(type(file), FileHandler):
            raise BadSubclassError('The file handler must be a subclass of FileHandler')

        if not issubclass(type(exampler), Exampler):
            raise BadSubclassError('The model exampler must be a subclass of Exampler')

        self.web = web
        self.file = file
        self.exampler = exampler
        self.passports_filename = nsi_passports['file']
        self.passports_modelname = nsi_passports['model']
        self.passports_fields: Iterable[str] | None = nsi_passports.get('fields')
        self.nsi_dicts = nsi_dicts
        self.do_not_use_nested_data = do_not_use_nested_data
        self.forced_update = forced_update
        self.dict_internal_pk_field = dict_internal_pk_field
        # Название поля модели паспортов связанной со справочниками
        self.passports_rel_field = passports_rel_field

        if self.do_not_use_nested_data:
            # NOTE(Ars): Для алгоритма без проваливания во вложенную data
            self.DICT_PK_NAMES = {'ID', 'RECID', 'CODE', 'code', 'id', 'depart_oid'}
        else:
            # NOTE(Ars): Возможно для стабильности лучше было бы сделать
            # определенный порядок проверки ключей
            self.DICT_PK_NAMES = {'ID', 'RECID', 'CODE', 'id', 'depart_oid'}

        # XXX(Ars): Как красивее обыграть?
        self.exampler.DICT_PK_NAMES = self.DICT_PK_NAMES

        self.passport_changed = False

    async def main(self) -> None:
        """Метод параллельного запуска обработки паспортов и самих справочников"""
        log.debug('')
        self.start_ts = time.time()

        passport_semaphore = asyncio.Semaphore(1)  # Allow 1 concurrent writers

        total_dicts = len(self.nsi_dicts)

        coros = []
        for dict_filename, meta_data in self.nsi_dicts.items():
            dict_state = DictState(
                dict_filename=dict_filename,
                # TODO(Ars): хранить Semaphore в атрибуте класса NsiDataHandler
                passport_semaphore=passport_semaphore,
                total_dicts=total_dicts,
                forced_update=dict_filename.split('.')[0] in self.forced_update,
                **meta_data,
            )
            log.debug('append coro for: %s', dict_filename)
            coros.append(self.passport_processing(dict_state))

        with rich_live:
            log.debug('gather start coros %s', coros)
            results = await asyncio.gather(*coros, return_exceptions=True)

        self.show_results(results)

    def show_results(self, results: list[tuple[str, DictState]]) -> None:
        log.info('Final Results:')

        self.exampler.show_passport_model(
            self.passports_modelname, self.dict_internal_pk_field, self.passport_changed
        )

        for i, (result) in enumerate(results, start=1):
            if isinstance(result, Exception):
                log.exception(
                    'result %s: %r',
                    i,
                    result,
                    exc_info=result if log.level == logging.DEBUG else False,
                )
            else:
                log.info('result %s: %r', i, f'{result[0]} - {result[1].dict_filename}')

                self.exampler.show_dict_model(result[1])

        log.info('Общее время выполнения: %.1fсек.', time.time() - self.start_ts)

    async def passport_processing(self, dict_state: DictState) -> tuple[str, DictState]:
        """Обрабатывает паспорт справочника."""
        log.debug('Обработка справочника: %s', dict_state.dict_filename)

        remote_passport = await self.web.get_remote_passport(dict_state)

        # NOTE(Ars): Для того чтобы определить единое расширение файла
        # для всех последующих корутин работающих конкурентно,
        # иначе это может привести к проблеме в которой условно
        # несколько корутин откроют .gz файлы, первая корутина
        # создаст .json файл а .gz файл удалит,
        # а остальные будут пытаться читать из несуществующего файла.
        async with dict_state.passport_semaphore:
            exists_code, filename = self.file.exists(self.passports_filename)

        match exists_code:
            case 'exists':
                # NOTE(Ars): semaphore нужен для того чтобы не давать читать и записывать
                # одновременно нескольким корутинам в один файл паспортов.
                async with dict_state.passport_semaphore:  # noqa: SIM117
                    #
                    # TODO(Ars): Добавить проверку чексуммы для объекта обновляемого паспорта,
                    # чтобы не перезаписывать файл попусту когда ничего не изменилось
                    async with self.file.overwrite_records(self.passports_filename) as local_passports:
                        #
                        local_passports = self.downgrade_passports_version(local_passports)

                        self.add_or_upd_passport(dict_state, remote_passport, local_passports)
            #
            case 'exists_but_another_ext':
                log.info('Чтение из: %s Запись в: %s', filename, dict_state.dict_filename)

                # NOTE(Ars): semaphore нужен для того чтобы не давать читать и записывать
                # одновременно нескольким корутинам в один файл паспортов.
                async with dict_state.passport_semaphore:
                    #
                    local_passports = await self.file.read_records(filename)

                local_passports = self.downgrade_passports_version(local_passports)

                self.add_or_upd_passport(dict_state, remote_passport, local_passports)

                # NOTE(Ars): semaphore нужен для того чтобы не давать читать и записывать
                # одновременно нескольким корутинам в один файл паспортов.
                async with dict_state.passport_semaphore:
                    await self.file.write_records(self.passports_filename, local_passports)
                    self.file.remove_file(filename)
            #
            case 'not_exists':
                local_passports = [self.build_passport(dict_state, remote_passport)]
                await self.file.write_records(self.passports_filename, local_passports)

        self.exampler.upd_passport_model_data_with_passport_fields(local_passports)

        dict_return_value = await self.dictionary_processing(dict_state, remote_passport)

        return (dict_return_value, dict_state)

    @staticmethod
    def downgrade_passports_version(local_passports: list[dict]) -> list[dict]:
        """Метод для тестирования."""
        # for p in local_passports:
        #     p['fields']['version'] = '0.0'
        return local_passports

    # TODO(Ars): Необходимо оптимизировать код в этом методе (много дублирования)
    async def dictionary_processing(self, dict_state: DictState, remote_passport: dict) -> str:
        """Обрабатывает справочник, создаёт/обновляет/пропускает работу с ними."""
        log.debug('Обработка паспорта для: %s', dict_state.dict_filename)

        exists_code, filename = self.file.exists(dict_state.dict_filename)

        match exists_code:
            case 'exists':
                #
                if not dict_state.version_changed and not dict_state.forced_update:
                    log.info('SKIPPED - %s', dict_state.dict_filename)
                    return 'SKIPPED'

                remote_dicts = await self.get_remote_dicts(dict_state)

                async with self.file.overwrite_records(
                    dict_state.dict_filename, remote_dicts
                ) as local_dicts:
                    #
                    associate_pk_map = self.build_associate_map_internal_and_external_pk(local_dicts)

                    # ссылка в менеджере будет ссылаться на обновленный объект
                    remote_dicts = self.update_remote_dicts(
                        dict_state,
                        remote_dicts,
                        remote_passport,
                        lambda record_data: associate_pk_map[self.get_dict_record_pk(record_data)],
                    )

                if dict_state.create_sql:
                    associate_fields_to_types_map = self.build_associate_map_fields_to_types(
                        remote_dicts
                    )
                    sql_records = self.build_sql_records(
                        dict_state, remote_dicts, associate_fields_to_types_map
                    )
                    await self.file.write_sql_records(dict_state.dict_filename, sql_records)

                self.exampler.upd_dict_model_data_with_passport_fields(dict_state, remote_passport)
                dict_state.dict_changed = True
                self.passport_changed = True

                log.info('UPDATED - %s', dict_state.dict_filename)
                return 'UPDATED'

            case 'exists_but_another_ext':
                #
                log.info('Чтение из: %s Запись в: %s', filename, dict_state.dict_filename)

                remote_dicts = await self.get_remote_dicts(dict_state)

                if not dict_state.version_changed and not dict_state.forced_update:
                    local_dicts = await self.file.read_records(filename)
                    await self.file.write_records(dict_state.dict_filename, local_dicts)
                    self.file.remove_file(filename)

                    if dict_state.create_sql:
                        associate_pk_map = self.build_associate_map_internal_and_external_pk(local_dicts)

                        remote_dicts = self.update_remote_dicts(
                            dict_state,
                            remote_dicts,
                            remote_passport,
                            lambda record_data: associate_pk_map[self.get_dict_record_pk(record_data)],
                        )

                        associate_fields_to_types_map = self.build_associate_map_fields_to_types(
                            remote_dicts
                        )
                        sql_records = self.build_sql_records(
                            dict_state, remote_dicts, associate_fields_to_types_map
                        )
                        await self.file.write_sql_records(dict_state.dict_filename, sql_records)
                        filename_with_new_ext = filename.replace('.json', '.sql')
                        self.file.remove_file(filename_with_new_ext)

                    log.info('SKIPPED+EXT - %s', dict_state.dict_filename)
                    return 'SKIPPED+EXT'

                local_dicts = await self.file.read_records(filename)

                associate_pk_map = self.build_associate_map_internal_and_external_pk(local_dicts)

                # ссылка в менеджере будет ссылаться на обновленный объект
                remote_dicts = self.update_remote_dicts(
                    dict_state,
                    remote_dicts,
                    remote_passport,
                    lambda record_data: associate_pk_map[self.get_dict_record_pk(record_data)],
                )

                await self.file.write_records(dict_state.dict_filename, remote_dicts)
                self.file.remove_file(filename)

                if dict_state.create_sql:
                    associate_fields_to_types_map = self.build_associate_map_fields_to_types(
                        remote_dicts
                    )
                    sql_records = self.build_sql_records(
                        dict_state, remote_dicts, associate_fields_to_types_map
                    )
                    await self.file.write_sql_records(dict_state.dict_filename, sql_records)
                    filename_with_new_ext = filename.replace('.json', '.sql')
                    self.file.remove_file(filename_with_new_ext)

                self.exampler.upd_dict_model_data_with_passport_fields(dict_state, remote_passport)
                dict_state.dict_changed = True
                self.passport_changed = True

                log.info('UPDATED+EXT - %s', dict_state.dict_filename)
                return 'UPDATED+EXT'

            case 'not_exists':
                remote_dicts = await self.get_remote_dicts(dict_state)

                remote_dicts = self.update_remote_dicts(
                    dict_state,
                    remote_dicts,
                    remote_passport,
                    lambda _: {'pk': str(uuid.uuid4())},
                )
                await self.file.write_records(dict_state.dict_filename, remote_dicts)

                if dict_state.create_sql:
                    associate_fields_to_types_map = self.build_associate_map_fields_to_types(
                        remote_dicts
                    )
                    sql_records = self.build_sql_records(
                        dict_state, remote_dicts, associate_fields_to_types_map
                    )
                    await self.file.write_sql_records(dict_state.dict_filename, sql_records)

                self.exampler.upd_dict_model_data_with_passport_fields(dict_state, remote_passport)
                dict_state.dict_changed = True
                self.passport_changed = True

                log.info('CREATED - %s', dict_state.dict_filename)
                return 'CREATED'

    async def change_file_extension(self, dict_state: DictState, filename: str) -> None:
        """"""
        log.info('Чтение из: %s Запись в: %s', filename, dict_state.dict_filename)
        local_dicts = await self.file.read_records(filename)
        await self.file.write_records(dict_state.dict_filename, local_dicts)
        self.file.remove_file(filename)

    def add_or_upd_passport(
        self,
        dict_state: DictState,
        remote_passport: dict,
        local_passports: list[dict],
    ) -> None:
        """Перезаписывает объект паспорта из внутренней системы данными из внешней системы."""
        log.debug('Апдейт для: %s', dict_state.dict_filename)
        for i, passport in enumerate(local_passports):
            if dict_state.oid == passport['fields']['oid']:
                dict_state.version_changed = (
                    local_passports[i]['fields']['version'] != remote_passport['version']
                )
                # Обновляем только если версия справочника изменилась
                if dict_state.version_changed or dict_state.forced_update:
                    dict_state.passport_pk = local_passports[i]['pk']
                    local_passports[i] = self.build_passport(dict_state, remote_passport)
                break
        else:
            local_passports.append(self.build_passport(dict_state, remote_passport))

    async def get_remote_dicts(self, dict_state: DictState) -> list[dict]:
        """Получает данные справочника из внешней системы."""
        log.debug('Сформировать remote_dicts для: %s', dict_state.dict_filename)

        dict_versions = await self.web.get_dict_versions(dict_state)

        latest_dict_version = dict_versions['list'][0]['version']

        zip_filename = await self.web.get_zip_filename(dict_state, latest_dict_version)
        zip_buffer = await self.web.download_zip(dict_state, zip_filename)

        remote_dicts = self.file.get_records_from_zip_file(dict_state, zip_buffer)

        self.check_dict_pk_names(remote_dicts)

        remote_dicts = self.filter_remote_dicts(dict_state.filter_func, remote_dicts)
        return self.filter_remote_dicts_fields(dict_state.fields, remote_dicts)

    def check_dict_pk_names(self, remote_dicts: list[dict]) -> None:
        """Проверка что хотябы один ключ из DICT_PK_NAMES присутствует в dict_data"""
        for record in remote_dicts:
            dict_data = self.get_dict_record_data(record)
            dict_data_keys = dict_data.keys()
            if not self.DICT_PK_NAMES.intersection(dict_data_keys):
                log.warning('dict_data: %r', dict_data)
                raise NsiPkNotFoundError(
                    f'Уникальный идентификатор не найден по ключам: {self.DICT_PK_NAMES!r} '
                    f'в ключах справочника: {list(dict_data_keys)}'
                )

    def update_remote_dicts(
        self,
        dict_state: DictState,
        remote_dicts: list[dict],
        remote_passport: dict,
        pk_getter: Callable[[dict], dict],
    ) -> list[dict]:
        """
        Перезаписывает объект из внешней системы данными
        необходимыми для сохранения во внутреннюю систему.
        """
        log.debug('')
        # remote_passport_fields: set[str] = {f['field'] for f in remote_passport['fields']}

        for i, __record in enumerate(remote_dicts):
            dict_data = self.get_dict_record_data(__record)

            self.exampler.upd_dict_model_data_with_dict_fields(dict_state, dict_data)

            dict_data = {k.replace('-', '_'): v for k, v in dict_data.items()}

            # Не факт что это необходимо, но если в справочнике будут лишние поля то это их отфильтрует
            # dict_data = {k: v for k, v in dict_data.items() if k in remote_passport_fields}

            data = pk_getter(dict_data)

            # dict_data.update({self.passports_rel_field: dict_state.passport_pk})
            # NOTE(Ars): Для того чтобы passports_rel_field оказался первым ключем (для читаемости)
            dict_data = {self.passports_rel_field: dict_state.passport_pk, **dict_data}

            remote_dicts[i] = {
                'model': dict_state.model,
                'pk': data['pk'],
                'fields': dict_data,
            }
        return remote_dicts

    def build_associate_map_fields_to_types(self, remote_dicts: list[dict]) -> dict[str, Callable]:
        """"""
        associate_map = {}
        for record in remote_dicts:
            for k, v in record['fields'].items():
                if k not in associate_map:
                    associate_map[k] = type(v)
        return associate_map

    def build_associate_map_internal_and_external_pk(self, local_dicts: list[dict]) -> dict:
        """Соотносит уникальные идентификаторы из внешней и внутренней систем."""
        log.debug('')
        return {
            self.get_dict_record_pk(record['fields']): {'pk': record['pk']} for record in local_dicts
        }

    def get_dict_record_data(self, record: dict) -> dict[str, Any]:
        if self.do_not_use_nested_data:
            record.pop('data', None)
            return record
        # Если существует вложенная data у записи,
        # то лучше использовать её, потому что в ней больше полей.
        return record.get('data', record)

    def get_dict_record_pk(self, fields: dict) -> int | str:
        """Ищет уникальный идентификатор записи используя разные ключи."""
        for pk_name in self.DICT_PK_NAMES:
            pk_value = fields.get(pk_name)
            if pk_value is not None:
                return pk_value
        else:
            log.warning('fields: %r', fields)
            raise NsiPkNotFoundError(
                f'Уникальный идентификатор не найден по ключам: {self.DICT_PK_NAMES!r} '
                f'в ключах справочника: {list(fields.keys())}'
            )

    def filter_remote_dicts(
        self, filter_func: Callable[[dict], bool], remote_dicts: list[dict]
    ) -> list[dict]:
        if filter_func is not None:
            remote_dicts = list(
                filter(lambda r: filter_func(self.get_dict_record_data(r)), remote_dicts)
            )
        return remote_dicts

    def filter_remote_dicts_fields(
        self, fields: Iterable[str] | None, remote_dicts: list[dict]
    ) -> list[dict]:
        if fields is not None:
            fields_with_reserved_keys = self.DICT_PK_NAMES.union(fields)
            for i, record in enumerate(remote_dicts):
                record = self.get_dict_record_data(record)
                remote_dicts[i] = {k: v for k, v in record.items() if k in fields_with_reserved_keys}
        return remote_dicts

    def filter_remote_passport_fields(self, passport_fields: dict) -> dict:
        if self.passports_fields is not None:
            fields_with_reserved_keys = self.PASSPORT_RESERVED_FIELDS.union(self.passports_fields)
            passport_fields = {
                k: v for k, v in passport_fields.items() if k in fields_with_reserved_keys
            }
        return passport_fields

    def build_sql_records(
        self, dict_state: DictState, remote_dicts: list[dict], associate_map: dict[str, Callable]
    ) -> str:
        """
        https://www.postgresql.org/docs/current/sql-copy.html
        """
        log.debug('')
        # NOTE(Ars): Возможно для стабильности работы придется сортировать ключи
        # по своему усмотрению, чтобы nsi не начудил

        for field_name, field_type in associate_map.items():
            # NOTE(Ars): NULL в SQL файле для COPY по умолчанию записываеться как '\N'
            if field_type() is None:
                associate_map[field_name] = lambda: r'\N'
            # NOTE(Ars): Пустая строка "" в файле представлена как два знака табуляции: \t\t
            # Пустая строка это отсутствие знака между
            # двумя разделительными табуляциями \t""\t == \t\t\t\t
            elif isinstance(field_type, str):
                associate_map[field_name] = lambda: r'\t\t'

        # NOTE(Ars): Для SQL фикстуры не получиться использовать passports_rel_field
        # как алиас для passports_rel_field + _id, поля должны
        # совпадать по имени с тем что действительно записано в БД.
        associate_map[f'{self.passports_rel_field}_id'] = associate_map.pop(self.passports_rel_field)
        for remote_dict in remote_dicts:
            remote_dict['fields'] = {
                f'{self.passports_rel_field}_id': remote_dict['fields'].pop(self.passports_rel_field),
                # Для того чтобы passports_rel_field + _id переместился в начало словаря
                **remote_dict['fields'],
            }

        dict_model = dict_state.model.lower().replace('.', '_')
        dict_model = f'public.{dict_model}'

        # NOTE(Ars): Для SQL фикстуры не получиться использовать pk как алиас для dict_internal_pk_field,
        # поля должны совпадать по имени с тем что действительно записано в БД.
        fields = (self.dict_internal_pk_field, *associate_map.keys())

        # NOTE(Ars): Postgres приводит все названия полей к нижнему регистру
        # если не поместить их в двойные кавычки.
        solid_fields = ', '.join(f'"{f}"' for f in fields)

        start_of_data_marker = ';'
        end_of_data_marker = r'\.'

        sql_query = f'COPY {dict_model} ({solid_fields}) FROM stdin{start_of_data_marker}'

        # Добавления недостающих полей
        for remote_dict in remote_dicts:
            dict_fields: dict = remote_dict['fields']
            updated_fields = {}

            for field_name, field_type in associate_map.items():
                if field_name in dict_fields:
                    updated_fields[field_name] = dict_fields[field_name]
                else:
                    # Добавляем значение по умолчанию для недостающего поля
                    updated_fields[field_name] = field_type()

            # Обновляем поля в правильном порядке
            remote_dict['fields'] = updated_fields

        rows = [sql_query]
        for remote_dict in remote_dicts:
            values = '\t'.join(str(v) for v in remote_dict['fields'].values())
            row = f'{remote_dict["pk"]}\t{values}'
            rows.append(row)

        rows.append(end_of_data_marker)

        return '\n'.join(rows)

    @abc.abstractmethod
    def build_passport(self, dict_state: DictState, remote_passport: dict) -> dict:
        """Базовая реализация объекта паспорта."""
        log.debug('')
        return {
            'model': self.passports_modelname,
            'pk': dict_state.passport_pk,
            'fields': {
                'fullName': remote_passport['fullName'],
                'shortName': remote_passport['shortName'],
                'version': remote_passport['version'],
                'createDate': remote_passport['createDate'],
                'publishDate': remote_passport['publishDate'],
                'lastUpdate': remote_passport['lastUpdate'],
                'approveDate': remote_passport['approveDate'],
                'rowsCount': remote_passport['rowsCount'],
                'description': remote_passport['description'],
                'releaseNotes': remote_passport['releaseNotes'],
                'structureNotes': remote_passport['structureNotes'],
                'fields': remote_passport['fields'],
                'laws': remote_passport['laws'],
                'hierarchical': remote_passport['hierarchical'],
                'identifier': remote_passport['identifier'],
                'oid': remote_passport['oid'],
            },
        }


class OfficialNsiDataHandler(NsiDataHandler):
    """Класс для определения логики относящейся к обработке данных от официального апи."""

    def build_passport(self, dict_state: DictState, remote_passport: dict) -> dict:
        """Добавляет доп ключи к записи, со значениями специфичными для официальных данных."""
        passport = super().build_passport(dict_state, remote_passport)
        oid_additional = next(
            (item['value'] for item in remote_passport['codes'] if item['type'] == 'TYPE_OTHER'), ''
        )
        passport['fields'].update(
            {
                'additionalOids': oid_additional,
                'groupId': remote_passport['groupId'],
                'authOrganizationId': remote_passport['authOrganizationId'],
                'respOrganizationId': remote_passport['respOrganizationId'],
                'typeId': remote_passport['typeId'],
                'keys': remote_passport['keys'],
                'result': remote_passport['result'],
                'resultCode': remote_passport['resultCode'],
                'resultText': remote_passport['resultText'],
                'nsiDictionaryId': remote_passport['nsiDictionaryId'],
                'archive': remote_passport['archive'],
            }
        )
        passport['fields'] = self.filter_remote_passport_fields(passport['fields'])
        return passport


class PirateNsiDataHandler(NsiDataHandler):
    """Класс для определения логики относящейся к обработке данных от пиратского апи."""

    def build_passport(self, dict_state: DictState, remote_passport: dict) -> dict:
        """Добавляет доп ключи к записи, со значениями специфичными для пиратских данных."""
        passport = super().build_passport(dict_state, remote_passport)
        passport['fields'].update(
            {
                'additionalOids': remote_passport['additionalOids'],
                'groupId': remote_passport['group']['id'],
                # NOTE(Ars): Значение не сходиться
                'authOrganizationId': 0,  # remote_passport['authOrganization']['id'],
                'respOrganizationId': 0,  # remote_passport['respOrganization']['id'],
                # NOTE(Ars): Эти поля отсутствуют в ответе пиратского апи.
                'typeId': 0,
                'keys': [],
                'result': '',
                'resultCode': 0,
                'resultText': '',
                'nsiDictionaryId': 0,
                'archive': False,
            }
        )
        passport['fields'] = self.filter_remote_passport_fields(passport['fields'])
        return passport
