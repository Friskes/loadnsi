import abc
from datetime import datetime, timezone
from typing import Any

from .dtos import DictState
from .exceptions import NsiPkNotFoundError
from .file_handlers import FileHandler
from .logger import log


class Exampler(abc.ABC):
    """"""

    @abc.abstractmethod
    def show_passport_model(
        self, passports_modelname: str, dict_internal_pk_field: str, passport_changed: bool
    ) -> None:
        raise NotImplementedError

    @abc.abstractmethod
    def show_dict_model(self, dict_state: DictState) -> None:
        raise NotImplementedError

    @abc.abstractmethod
    def upd_passport_model_data_with_passport_fields(self, local_passports: list[dict]) -> None:
        raise NotImplementedError

    @abc.abstractmethod
    def upd_dict_model_data_with_passport_fields(
        self, dict_state: DictState, remote_passport: dict
    ) -> None:
        raise NotImplementedError

    @abc.abstractmethod
    def upd_dict_model_data_with_dict_fields(
        self, dict_state: DictState, dict_data: dict[str, Any]
    ) -> None:
        raise NotImplementedError


# TODO(Ars): Необходимо решить проблему при которой
# при обновлении части справочников ими перезатирается файл с примерами всех справочников.
# Как вариант можно разбивать файл примеров по \n\n находить нужный справочник в списке
# и обновлять только этот элемент в списке, затем перезаписывать файл с 0 всеми справочниками из списка.
class ModelExampler(Exampler):
    """"""

    py_type_to_field_cls_name = {
        type(None): '!!!!!!!!!!!!!!!!!!!! UNKNOWN TYPE !!!!!!!!!!!!!!!!!!!!',
        # NOTE(Ars): Можно добавить TextField для особо больших max_length
        str: 'CharField',
        # NOTE(Ars): В принципе если считать макс значение integer
        # то можно будет выбирать SmallIntegerField или IntegerField или BigIntegerField
        int: 'IntegerField',
        bool: 'BooleanField',
        dict: 'JSONField',
        list: 'JSONField',
    }
    DICT_PK_NAMES: set[str]

    def __init__(
        self,
        file: FileHandler,
        show_model_data: str | None,
        model_data_params: str | None,
        models_filename: str = 'models_example.py',
        parent_dict_cls: str = '',
    ) -> None:
        self.file = file
        self.show_model_data = show_model_data
        self.model_data_params = model_data_params
        self.passport_model_data: dict[str, dict[str, Any]] = {}
        self.models_filename = models_filename
        self.parent_dict_cls = f'({parent_dict_cls})' if parent_dict_cls else ''

    def _construct_passports_model_cls(
        self, passports_modelname: str, dict_internal_pk_field: str
    ) -> str:
        """"""
        log.debug('Сборка класса модели паспортов.')

        model_cls_name = passports_modelname.rsplit('.', 1)[-1]

        ruff_noqa_line = '# ruff: noqa'

        cur_time = datetime.now(tz=timezone.utc).strftime('%Y-%m-%d %H:%M:%S')  # noqa: UP017
        time_line = f'# File creation time: {cur_time}\n\n'

        declaring_cls_line = f'class {model_cls_name}(models.Model):'

        doc_line = '    """Паспорты справочников НСИ."""\n'

        cls_lines = [ruff_noqa_line, time_line, declaring_cls_line, doc_line]

        for field_name, field_params in self.passport_model_data.items():
            params = []
            max_length = field_params.get('field_length')
            if max_length is not None:
                params.append(f'max_length={max_length}')

            if issubclass(field_params['field_type'], (dict, list)):
                params.append(f'default={field_params["field_type"].__name__}')

            if (
                self.model_data_params is not None
                and 'all_fields_not_required' in self.model_data_params
            ):
                if issubclass(field_params['field_type'], bool):
                    params.append('default=False')
                elif issubclass(field_params['field_type'], str):
                    params.append('blank=True')
                elif issubclass(field_params['field_type'], int):
                    params.append('null=True')

            field_cls_name = self.py_type_to_field_cls_name[field_params['field_type']]

            concat_params = ', '.join(params)
            model_field = f'    {field_name} = models.{field_cls_name}({concat_params})'
            cls_lines.append(model_field)

        declaring_meta_cls_line = '\n    class Meta:\n'
        meta_cls_fields = []
        ordering = f"        ordering = ('{dict_internal_pk_field}',)"
        meta_cls_fields.append(ordering)
        meta_cls_fields = '\n'.join(meta_cls_fields)
        meta_cls = f'{declaring_meta_cls_line}{meta_cls_fields}'
        cls_lines.append(meta_cls)

        return '\n'.join(cls_lines)

    def _construct_dict_model_cls(self, dict_state: DictState) -> str:
        """"""
        log.debug('Сборка класса модели справочника для: %s', dict_state.dict_filename)

        model_cls_name = dict_state.model.rsplit('.', 1)[-1]

        declaring_cls_line = f'class {model_cls_name}{self.parent_dict_cls}:'

        doc_line = f'    """{dict_state.passport_name}"""\n'

        cls_lines = [declaring_cls_line, doc_line]

        for field_name, field_params in dict_state.dict_model_data.items():
            params = []
            max_length = field_params.get('field_length')
            if max_length is not None:
                params.append(f'max_length={max_length}')

            if issubclass(field_params['field_type'], (dict, list)):
                params.append(f'verbose_name={field_params.get("alias", "")!r}')
                params.append(f'default={field_params["field_type"].__name__}')
            else:
                params.append(f'{field_params["alias"]!r}')

            if (
                self.model_data_params is not None
                and 'all_fields_not_required' in self.model_data_params
            ):
                if issubclass(field_params['field_type'], bool):
                    params.append('default=False')
                elif issubclass(field_params['field_type'], str):
                    params.append('blank=True')
                elif issubclass(field_params['field_type'], int):
                    params.append('null=True')

            not_required_params = ('default=False', 'blank=True', 'null=True')
            if (
                not field_params.get('isrequired')
                and not issubclass(field_params['field_type'], str)
                and not any(p in params for p in not_required_params)
            ):
                params.append('null=True')

            field_cls_name = self.py_type_to_field_cls_name[field_params['field_type']]

            concat_params = ', '.join(params)
            model_field = f'    {field_name} = models.{field_cls_name}({concat_params})'
            cls_lines.append(model_field)

        for pk_name in self.DICT_PK_NAMES:
            if pk_name in dict_state.dict_model_data:
                nsi_pk_name = pk_name
                break
        else:
            raise NsiPkNotFoundError(
                f'Уникальный идентификатор не найден по ключам: {self.DICT_PK_NAMES!r} '
                f'в ключах справочника: {list(dict_state.dict_model_data.keys())}'
            )

        declaring_meta_cls_line = '\n    class Meta:\n'
        meta_cls_fields = []
        ordering = f"        ordering = ('{nsi_pk_name}',)"
        meta_cls_fields.append(ordering)
        meta_cls_fields = '\n'.join(meta_cls_fields)
        meta_cls = f'{declaring_meta_cls_line}{meta_cls_fields}'
        cls_lines.append(meta_cls)

        return '\n'.join(cls_lines)

    def upd_dict_model_data_with_passport_fields(
        self, dict_state: DictState, remote_passport: dict
    ) -> None:
        """"""
        if not self.show_model_data:
            return
        log.debug('Обновление dict_model_data')

        if not dict_state.passport_name:
            dict_state.passport_name = remote_passport['shortName']

        for field_data in remote_passport['fields']:
            key = field_data['field']
            if key == 'extId':
                for pk_name in self.DICT_PK_NAMES:
                    if pk_name in dict_state.dict_model_data:
                        key = pk_name
                        break
                else:
                    raise NsiPkNotFoundError(
                        f'Уникальный идентификатор не найден по ключам: {self.DICT_PK_NAMES!r} '
                        f'в ключах справочника: {list(dict_state.dict_model_data.keys())}'
                    )

            for k in (
                'isrequired',
                'alias',
                # 'dataType',
            ):
                try:
                    dict_state.dict_model_data[key][k] = field_data[k]
                except KeyError as exc:
                    log.warning(
                        'Ключ: %s есть в fields паспорта справочника, '
                        'но отсутствует в реальном файле справочника: %s '
                        'по этому, такое поле не будет добавлено в пример класса модели.',
                        exc,
                        dict_state.dict_filename,
                    )

        dict_state.dict_model_data = {
            k.replace('-', '_'): v for k, v in dict_state.dict_model_data.items()
        }

    def upd_dict_model_data_with_dict_fields(
        self, dict_state: DictState, dict_data: dict[str, Any]
    ) -> None:
        """"""
        if not self.show_model_data:
            return

        for field_name, field_value in dict_data.items():
            #
            if field_name not in dict_state.dict_model_data:
                dict_state.dict_model_data[field_name] = {}

            type_field_value = type(field_value)
            if 'field_type' not in dict_state.dict_model_data[field_name]:
                dict_state.dict_model_data[field_name]['field_type'] = type_field_value

            if issubclass(type_field_value, str):
                len_field_value = len(field_value)

                if dict_state.dict_model_data[field_name].get('field_length', -1) < len_field_value:
                    dict_state.dict_model_data[field_name]['field_length'] = max(len_field_value, 1)

    def upd_passport_model_data_with_passport_fields(self, local_passports: list[dict]) -> None:
        """"""
        if not self.show_model_data:
            return
        log.debug('Обновление passport_model_data')

        for passport in local_passports:
            #
            for field_name, field_value in passport['fields'].items():
                #
                if field_name not in self.passport_model_data:
                    self.passport_model_data[field_name] = {}

                type_field_value = type(field_value)
                if 'field_type' not in self.passport_model_data[field_name]:
                    self.passport_model_data[field_name]['field_type'] = type_field_value

                if issubclass(type_field_value, str):
                    len_field_value = len(field_value)

                    if self.passport_model_data[field_name].get('field_length', -1) < len_field_value:
                        self.passport_model_data[field_name]['field_length'] = max(len_field_value, 1)

        self.passport_model_data = {k.replace('-', '_'): v for k, v in self.passport_model_data.items()}

    def show_passport_model(
        self, passports_modelname: str, dict_internal_pk_field: str, passport_changed: bool
    ) -> None:
        """"""
        if not passport_changed:
            return

        if self.show_model_data == 'stdout':
            model_cls = self._construct_passports_model_cls(passports_modelname, dict_internal_pk_field)
            print(model_cls)
        elif self.show_model_data == 'file':
            model_cls = self._construct_passports_model_cls(passports_modelname, dict_internal_pk_field)
            self.file.remove_file(self.models_filename)
            self.file.write_text(self.models_filename, f'{model_cls}\n\n\n')

    def show_dict_model(self, dict_state: DictState) -> None:
        """"""
        if not dict_state.dict_changed:
            return

        if self.show_model_data == 'stdout':
            model_cls = self._construct_dict_model_cls(dict_state)
            print(model_cls)
        elif self.show_model_data == 'file':
            model_cls = self._construct_dict_model_cls(dict_state)
            self.file.write_text(self.models_filename, f'{model_cls}\n\n\n')
