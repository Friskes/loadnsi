import abc
import asyncio
import logging
import time
import uuid
from collections.abc import Callable, Iterable, Mapping
from typing import Any

from .console_view import rich_live
from .dtos import DictState
from .exceptions import BadSubclassError, NsiPkNotFoundError, SkipWriteFileError
from .file_handlers import FileHandler
from .logger import log
from .model_examplers import Exampler
from .sql_builders import Builder
from .web_handlers import NsiWebCrawler


async def safe_wrapper(coro, *args):
    """Возвращает результат ошибки корутины вместе с её аргументами."""
    try:
        return await coro(*args)
    except Exception as e:
        return (e, args)


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
        builder: Builder,
        nsi_passports: dict[str, str],
        nsi_dicts: dict[str, dict],
        do_not_use_nested_data: bool,
        forced_update: tuple[str, ...],
        lowercase_fields: bool,
        dict_internal_pk_field: str,
        passports_rel_field: str,
    ) -> None:
        if not issubclass(type(web), NsiWebCrawler):
            raise BadSubclassError('The web handler must be a subclass of NsiWebCrawler')

        if not issubclass(type(file), FileHandler):
            raise BadSubclassError('The file handler must be a subclass of FileHandler')

        if not issubclass(type(exampler), Exampler):
            raise BadSubclassError('The model exampler must be a subclass of Exampler')

        if not issubclass(type(builder), Builder):
            raise BadSubclassError('The model builder must be a subclass of Builder')

        self.web = web
        self.file = file
        self.exampler = exampler
        self.builder = builder
        self.passports_filename = nsi_passports['file']
        self.passports_modelname = nsi_passports['model']

        self.passports_include: Iterable[str] | None = nsi_passports.get('include')
        self.passports_exclude: Iterable[str] | None = nsi_passports.get('exclude')
        if self.passports_include and self.passports_exclude:
            raise ValueError('Одновременное использование параметров include и exclude запрещено.')

        self.nsi_dicts = nsi_dicts
        self.do_not_use_nested_data = do_not_use_nested_data
        self.forced_update = forced_update
        self.lowercase_fields = lowercase_fields
        self.dict_internal_pk_field = dict_internal_pk_field
        # Название поля модели паспортов связанной со справочниками
        self.passports_rel_field = passports_rel_field

        self.DICT_PK_NAMES = ['ID', 'RECID', 'CODE', 'id', 'depart_oid', 'smocod']

        if self.do_not_use_nested_data:
            # NOTE(Ars): Для алгоритма без проваливания во вложенную data
            self.DICT_PK_NAMES.append('code')

        if self.lowercase_fields:
            self.DICT_PK_NAMES = [k.lower() for k in self.DICT_PK_NAMES]

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
            coros.append(safe_wrapper(self.passport_processing, dict_state))

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
            status, args = result
            if isinstance(status, Exception):
                log.exception(
                    'result %s: %r - %s',
                    i,
                    status,
                    args[0].dict_filename,
                    exc_info=status if log.level == logging.DEBUG else False,
                )
            else:
                log.info('result %s: %r', i, f'{status} - {args.dict_filename}')

                self.exampler.show_dict_model(args)

        log.info('Общее время выполнения: %.1fсек.', time.time() - self.start_ts)

    async def passport_processing(self, dict_state: DictState) -> tuple[str, DictState]:
        """Обрабатывает паспорт справочника."""
        log.debug('Обработка справочника: %s', dict_state.dict_filename)

        remote_passport = await self.web.get_remote_passport(dict_state)

        if self.lowercase_fields:
            remote_passport = self._to_lowercase_keys(remote_passport)

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
                    async with self.file.overwrite_records(self.passports_filename) as local_passports:
                        #
                        local_passports = self.downgrade_passports_version(local_passports)

                        skipped = self.add_or_upd_passport(dict_state, remote_passport, local_passports)
                        if skipped:
                            raise SkipWriteFileError(f'Пропуск записи файла: {self.passports_filename}')
            #
            case 'exists_but_another_ext':
                log.info('Чтение из: %s Запись в: %s', filename, dict_state.dict_filename)

                # NOTE(Ars): semaphore нужен для того чтобы не давать читать и записывать
                # одновременно нескольким корутинам в один файл паспортов.
                async with dict_state.passport_semaphore:
                    #
                    local_passports = await self.file.read_records(filename)

                local_passports = self.downgrade_passports_version(local_passports)

                skipped = self.add_or_upd_passport(dict_state, remote_passport, local_passports)

                if not skipped:
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
                    try:
                        sql_records = self.builder.build_sql_copy_update_insert(
                            dict_state, remote_dicts, associate_fields_to_types_map
                        )
                    except Exception as exc:
                        log.exception(
                            'Не удалось создать SQL фикстуру для: %s - %r',
                            dict_state.dict_filename,
                            exc,
                            exc_info=exc,
                        )
                    else:
                        await self.file.write_sql_records(dict_state.dict_filename, sql_records)

                if not dict_state.passport_name:
                    dict_state.passport_name = remote_passport[
                        'shortname' if self.lowercase_fields else 'shortName'
                    ]
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

                        try:
                            sql_records = self.builder.build_sql_copy_update_insert(
                                dict_state, remote_dicts, associate_fields_to_types_map
                            )
                        except Exception as exc:
                            log.exception(
                                'Не удалось создать SQL фикстуру для: %s - %r',
                                dict_state.dict_filename,
                                exc,
                                exc_info=exc,
                            )
                        else:
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

                    try:
                        sql_records = self.builder.build_sql_copy_update_insert(
                            dict_state, remote_dicts, associate_fields_to_types_map
                        )
                    except Exception as exc:
                        log.exception(
                            'Не удалось создать SQL фикстуру для: %s - %r',
                            dict_state.dict_filename,
                            exc,
                            exc_info=exc,
                        )
                    else:
                        await self.file.write_sql_records(dict_state.dict_filename, sql_records)
                        filename_with_new_ext = filename.replace('.json', '.sql')
                        self.file.remove_file(filename_with_new_ext)

                if not dict_state.passport_name:
                    dict_state.passport_name = remote_passport[
                        'shortname' if self.lowercase_fields else 'shortName'
                    ]
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

                    try:
                        sql_records = self.builder.build_sql_copy_update_insert(
                            dict_state, remote_dicts, associate_fields_to_types_map
                        )
                    except Exception as exc:
                        log.exception(
                            'Не удалось создать SQL фикстуру для: %s - %r',
                            dict_state.dict_filename,
                            exc,
                            exc_info=exc,
                        )
                    else:
                        await self.file.write_sql_records(dict_state.dict_filename, sql_records)

                if not dict_state.passport_name:
                    dict_state.passport_name = remote_passport[
                        'shortname' if self.lowercase_fields else 'shortName'
                    ]
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
    ) -> bool:
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
                    return False
                return True
        else:
            local_passports.append(self.build_passport(dict_state, remote_passport))
            return False

    async def get_remote_dicts(self, dict_state: DictState) -> list[dict]:
        """Получает данные справочника из внешней системы."""
        log.debug('Сформировать remote_dicts для: %s', dict_state.dict_filename)

        dict_versions = await self.web.get_dict_versions(dict_state)

        latest_dict_version = dict_versions['list'][0]['version']

        zip_filename = await self.web.get_zip_filename(dict_state, latest_dict_version)
        zip_buffer = await self.web.download_zip(dict_state, zip_filename)

        remote_dicts = self.file.get_records_from_zip_file(dict_state, zip_buffer)

        if self.lowercase_fields:
            remote_dicts = self._to_lowercase_keys(remote_dicts)

        self.check_dict_pk_names(remote_dicts)

        remote_dicts = self.filter_remote_dicts(dict_state.filter, remote_dicts)
        return self.filter_remote_dicts_fields(dict_state.include, dict_state.exclude, remote_dicts)

    def check_dict_pk_names(self, remote_dicts: list[dict]) -> None:
        """Проверка что хотябы один ключ из DICT_PK_NAMES присутствует в dict_data"""
        for record in remote_dicts:
            dict_data = self.get_dict_record_data(record)
            dict_data_keys = dict_data.keys()

            for pk_name in self.DICT_PK_NAMES:
                if pk_name in dict_data_keys:
                    break
            else:
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
        self, include: Iterable[str] | None, exclude: Iterable[str] | None, remote_dicts: list[dict]
    ) -> list[dict]:
        if include is not None:
            fields_with_reserved_keys = set(self.DICT_PK_NAMES).union(include)
            for i, record in enumerate(remote_dicts):
                record = self.get_dict_record_data(record)
                remote_dicts[i] = {k: v for k, v in record.items() if k in fields_with_reserved_keys}
        if exclude is not None:
            fields_without_reserved_keys = set(exclude).difference(self.DICT_PK_NAMES)
            for i, record in enumerate(remote_dicts):
                record = self.get_dict_record_data(record)
                remote_dicts[i] = {
                    k: v for k, v in record.items() if k not in fields_without_reserved_keys
                }
        return remote_dicts

    def filter_remote_passport_fields(self, passport_fields: dict) -> dict:
        if self.passports_include is not None:
            fields_with_reserved_keys = self.PASSPORT_RESERVED_FIELDS.union(self.passports_include)
            passport_fields = {
                k: v for k, v in passport_fields.items() if k in fields_with_reserved_keys
            }
        if self.passports_exclude is not None:
            fields_without_reserved_keys = set(self.passports_exclude).difference(
                self.PASSPORT_RESERVED_FIELDS
            )
            passport_fields = {
                k: v for k, v in passport_fields.items() if k not in fields_without_reserved_keys
            }
        return passport_fields

    def _to_lowercase_keys(self, data: dict | list[dict]) -> dict | list[dict]:
        if isinstance(data, Mapping):
            passport = {}
            for k, v in data.items():
                if k == 'fields':
                    fields = []
                    for field_data in data['fields']:
                        field_data['field'] = field_data['field'].lower()
                        fields.append(field_data)
                    v = fields
                passport[k.lower()] = v
            return passport
        if isinstance(data, Iterable):
            return [{k.lower(): v for k, v in self.get_dict_record_data(item).items()} for item in data]
        # ? Не стоит приводить к нижнему регистру вложенные поля?
        # stack = [data]
        # while stack:
        #     current = stack.pop()
        #     if isinstance(current, Mapping):
        #         for key in current:
        #             new_key = key.lower() if isinstance(key, str) else key
        #             current[new_key] = current.pop(key)
        #             stack.append(current[new_key])
        #     elif isinstance(current, Iterable):
        #         stack.extend(current)
        # return data

    def _construct_passport_fields(self, fields: Iterable[str], remote_passport: dict) -> dict:
        passport_fields = {}
        for field in fields:
            if self.lowercase_fields:
                field = field.lower()
            field_val = remote_passport.get(field)
            if field_val:
                passport_fields.update({field: field_val})
        return passport_fields

    @abc.abstractmethod
    def build_passport(self, dict_state: DictState, remote_passport: dict) -> dict:
        """Базовая реализация объекта паспорта."""
        log.debug('')
        default_passport_field_names = (
            'fullName',
            'shortName',
            'version',
            'createDate',
            'publishDate',
            'lastUpdate',
            'approveDate',
            'rowsCount',
            'description',
            'releaseNotes',
            'structureNotes',
            'fields',
            'laws',
            'hierarchical',
            'identifier',
            'oid',
        )
        fields = self._construct_passport_fields(default_passport_field_names, remote_passport)
        return {
            'model': self.passports_modelname,
            'pk': dict_state.passport_pk,
            'fields': fields,
        }


class OfficialNsiDataHandler(NsiDataHandler):
    """Класс для определения логики относящейся к обработке данных от официального апи."""

    def build_passport(self, dict_state: DictState, remote_passport: dict) -> dict:
        """Добавляет доп ключи к записи, со значениями специфичными для официальных данных."""
        passport = super().build_passport(dict_state, remote_passport)
        oid_additional_key = 'additionaloids' if self.lowercase_fields else 'additionalOids'
        oid_additional_val = next(
            (item['value'] for item in remote_passport['codes'] if item['type'] == 'TYPE_OTHER'), ''
        )
        passport_field_names = (
            'groupId',
            'authOrganizationId',
            'respOrganizationId',
            'typeId',
            'keys',
            'result',
            'resultCode',
            'resultText',
            'nsiDictionaryId',
            'archive',
        )
        fields = self._construct_passport_fields(passport_field_names, remote_passport)
        fields[oid_additional_key] = oid_additional_val
        passport['fields'].update(fields)
        passport['fields'] = self.filter_remote_passport_fields(passport['fields'])
        return passport


class PirateNsiDataHandler(NsiDataHandler):
    """Класс для определения логики относящейся к обработке данных от пиратского апи."""

    def build_passport(self, dict_state: DictState, remote_passport: dict) -> dict:
        """Добавляет доп ключи к записи, со значениями специфичными для пиратских данных."""
        passport = super().build_passport(dict_state, remote_passport)
        fields = {
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
        oid_additional_key = 'additionaloids' if self.lowercase_fields else 'additionalOids'
        oid_additional_val = remote_passport.get(oid_additional_key)
        if oid_additional_val:
            fields[oid_additional_key] = oid_additional_val
        fields = {k.lower(): v for k, v in fields.items()}
        passport['fields'].update(fields)
        passport['fields'] = self.filter_remote_passport_fields(passport['fields'])
        return passport
