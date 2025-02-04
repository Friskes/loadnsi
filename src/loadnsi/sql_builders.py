import abc
from collections.abc import Callable

try:
    from pypika import PostgreSQLQuery, Table
except ImportError:
    pass

from .dtos import DictState
from .logger import log


class Builder(abc.ABC):
    """"""

    @abc.abstractmethod
    def build_sql_copy_update_insert(
        self, dict_state: DictState, remote_dicts: list[dict], associate_map: dict[str, Callable]
    ) -> str:
        raise NotImplementedError


class SqlBuilder(Builder):
    """"""

    def __init__(
        self,
        dict_internal_pk_field: str,
        passports_rel_field: str,
    ) -> None:
        self.dict_internal_pk_field = dict_internal_pk_field
        # Название поля модели паспортов связанной со справочниками
        self.passports_rel_field = passports_rel_field
        self.sql_update_condition_fields = []

    def build_sql_copy_update_insert(
        self, dict_state: DictState, remote_dicts: list[dict], associate_map: dict[str, Callable]
    ) -> str:
        r"""
        Генерирует SQL формата::

            BEGIN;
            CREATE TEMP TABLE tmp_<таблица> AS TABLE <таблица> WITH NO DATA;
            ALTER TABLE <таблица> DISABLE TRIGGER ALL;
            COPY tmp_<таблица> ("<self.dict_internal_pk_field>", "field2", "field3") FROM STDIN WITH (FORMAT text);
            b962e385-610b-4d0a-806f-03311477a8f2	7026020	1.2.643.5.1.13.13.12.1.30.72291
            585193d7-d60a-4c5a-aa3e-d5cfd0f4bd77	7016244	1.2.643.5.1.13.13.12.4.16.63211
            \.

            UPDATE <таблица> AS t SET field2 = tmp.field2, field3 = tmp.field3 FROM tmp_<таблица> AS tmp WHERE t.<self.dict_internal_pk_field> = tmp.<self.dict_internal_pk_field>;
            INSERT INTO <таблица> SELECT * FROM tmp_<таблица> ON CONFLICT (<self.dict_internal_pk_field>) DO NOTHING;
            ALTER TABLE <таблица> ENABLE TRIGGER ALL;
            REINDEX TABLE <таблица>;
            COMMIT;

        https://www.postgresql.org/docs/current/sql-copy.html
        """  # noqa: E501
        log.debug('')

        sql_copy = self.build_sql_copy(dict_state, remote_dicts, associate_map)

        dict_model = dict_state.model.lower().replace('.', '_')

        fields_for_update = ', '.join(f'{f} = tmp.{f}' for f in associate_map)

        rows = []

        # Оборачиваем все запросы в одну транзакцию для ускорения
        begin_transaction = 'BEGIN;'
        # Создаем временную таблицу для обновления реальной таблицы
        create_temp_table = f'CREATE TEMP TABLE tmp_{dict_model} AS TABLE {dict_model} WITH NO DATA;'
        # Отключаем триггеры для ускорения вставки
        disable_trigger = f'ALTER TABLE {dict_model} DISABLE TRIGGER ALL;'

        # Позволяет определить в каком случае необходимо обновлять записи,
        # а именно когда значения в таблицах будут отличаться.
        if self.sql_update_condition_fields:
            conditions = ' OR '.join(
                f't.{f} IS DISTINCT FROM tmp.{f}' for f in self.sql_update_condition_fields
            )
            sql_update_condition = f' AND ({conditions})'
        else:
            sql_update_condition = ''

        # Обновляем записи
        update = (
            f'UPDATE {dict_model} AS t SET {fields_for_update} FROM tmp_{dict_model} AS tmp '
            f'WHERE t.{self.dict_internal_pk_field} = tmp.{self.dict_internal_pk_field}{sql_update_condition};'  # noqa: E501
        )
        # Добавляем новые записи если они появились
        insert = (
            f'INSERT INTO {dict_model} SELECT * FROM tmp_{dict_model} '
            f'ON CONFLICT ({self.dict_internal_pk_field}) DO NOTHING;'
        )
        # Включаем триггеры обратно
        enable_trigger = f'ALTER TABLE {dict_model} ENABLE TRIGGER ALL;'
        # Перестраиваем индексы
        reindex = f'REINDEX TABLE {dict_model};'
        # Завершаем транзакцию
        commit_transaction = 'COMMIT;'

        rows.append(begin_transaction)
        rows.append(create_temp_table)
        rows.append(disable_trigger)
        rows.append(sql_copy)
        rows.append(update)
        rows.append(insert)
        rows.append(enable_trigger)
        rows.append(reindex)
        rows.append(commit_transaction)

        return '\n'.join(rows)

    def build_sql_copy_merge_matched(self):
        r"""
        Генерирует SQL формата::

            BEGIN;
            CREATE TEMP TABLE tmp_<таблица> AS TABLE <таблица> WITH NO DATA;
            ALTER TABLE <таблица> DISABLE TRIGGER ALL;
            COPY tmp_<таблица> ("<self.dict_internal_pk_field>", "field2", "field3") FROM STDIN WITH (FORMAT text);
            b962e385-610b-4d0a-806f-03311477a8f2	7026020	1.2.643.5.1.13.13.12.1.30.72291
            585193d7-d60a-4c5a-aa3e-d5cfd0f4bd77	7016244	1.2.643.5.1.13.13.12.4.16.63211
            \.

            MERGE INTO <таблица> AS t USING tmp_<таблица> AS tmp ON t.<self.dict_internal_pk_field> = tmp.<self.dict_internal_pk_field>
            WHEN MATCHED THEN UPDATE SET field2 = tmp.field2, field3 = tmp.field3
            WHEN NOT MATCHED THEN INSERT (<self.dict_internal_pk_field>, field2, field3) VALUES (tmp.<self.dict_internal_pk_field>, tmp.field2, tmp.field3);
            ALTER TABLE <таблица> ENABLE TRIGGER ALL;
            REINDEX TABLE <таблица>;
            COMMIT;
        """  # noqa: E501

    def build_sql_copy(
        self, dict_state: DictState, remote_dicts: list[dict], associate_map: dict[str, Callable]
    ) -> str:
        r"""
        Генерирует SQL формата::

            COPY public.<таблица> ("<self.dict_internal_pk_field>", "field2", "field3") FROM STDIN WITH (FORMAT text);
            b962e385-610b-4d0a-806f-03311477a8f2	7026020	1.2.643.5.1.13.13.12.1.30.72291
            585193d7-d60a-4c5a-aa3e-d5cfd0f4bd77	7016244	1.2.643.5.1.13.13.12.4.16.63211
            \.

        https://www.postgresql.org/docs/current/sql-copy.html
        """  # noqa: E501
        log.debug('')

        self._changing_defaults_for_copy(associate_map)

        self._data_preparation(remote_dicts, associate_map)

        dict_model = dict_state.model.lower().replace('.', '_')
        # public_dict_model = f'public.{dict_model}'

        # NOTE(Ars): Для SQL фикстуры не получиться использовать pk как алиас для dict_internal_pk_field,
        # поля должны совпадать по имени с тем что действительно записано в БД.
        fields = (self.dict_internal_pk_field, *associate_map.keys())

        # NOTE(Ars): Postgres приводит все названия полей к нижнему регистру
        # если не поместить их в двойные кавычки.
        solid_fields = ', '.join(f'"{f}"' for f in fields)

        start_of_data_marker = ';'
        end_of_data_marker = '\\.\n'

        sql_query = (
            f'COPY {dict_model} ({solid_fields}) FROM STDIN WITH (FORMAT text){start_of_data_marker}'
        )

        rows = [sql_query]
        for remote_dict in remote_dicts:
            values = '\t'.join(str(v) for v in remote_dict['fields'].values())
            row = f'{remote_dict["pk"]}\t{values}'
            rows.append(row)

        rows.append(end_of_data_marker)

        return '\n'.join(rows)

    def build_sql_insert_or_update(
        self, dict_state: DictState, remote_dicts: list[dict], associate_map: dict[str, Callable]
    ) -> str:
        """
        Генерирует SQL формата::

            INSERT INTO "<таблица>" ("<self.dict_internal_pk_field>","field2","field3") VALUES ('7bf3449a-2ae8-49cc-bdc6-c380f7aa1918',362,'239'),('df349786-c9f0-485f-9326-2946b0f21424',364,'239') ON CONFLICT ("<self.dict_internal_pk_field>") DO UPDATE SET "field2"=EXCLUDED."field2","field3"=EXCLUDED."field3";
        """  # noqa: E501
        log.debug('')

        self._data_preparation(remote_dicts, associate_map)

        dict_model = dict_state.model.lower().replace('.', '_')

        # NOTE(Ars): Для SQL фикстуры не получиться использовать pk как алиас для dict_internal_pk_field,
        # поля должны совпадать по имени с тем что действительно записано в БД.
        fields = (self.dict_internal_pk_field, *associate_map.keys())

        table = Table(dict_model)
        values = ((remote_dict['pk'], *remote_dict['fields'].values()) for remote_dict in remote_dicts)
        query = (
            PostgreSQLQuery.into(table)
            .columns(*fields)
            .insert(*values)
            .on_conflict(getattr(table, self.dict_internal_pk_field))
        )
        for field_name in associate_map:
            query = query.do_update(field_name, None)  # None это EXCLUDED."field_name"
        return f'{query.get_sql()};'

    def _data_preparation(self, remote_dicts: list[dict], associate_map: dict[str, Callable]) -> None:
        """"""
        # NOTE(Ars): Возможно для стабильности работы придется сортировать ключи
        # по своему усмотрению, чтобы nsi не начудил

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

    def _changing_defaults_for_copy(self, associate_map: dict[str, Callable]) -> None:
        """"""
        for field_name, field_type in associate_map.items():
            # NOTE(Ars): NULL в SQL файле для COPY по умолчанию записываеться как '\N'
            if field_type() is None:
                associate_map[field_name] = lambda: r'\N'
            # NOTE(Ars): Пустая строка "" в файле представлена как два знака табуляции: \t\t
            # Пустая строка это отсутствие знака между
            # двумя разделительными табуляциями \t""\t == \t\t\t\t
            elif isinstance(field_type, str):
                associate_map[field_name] = lambda: r'\t\t'
