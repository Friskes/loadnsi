import abc
import gzip
import io
import json
import os
import zipfile
from contextlib import asynccontextmanager
from pathlib import Path

import aiofiles
import aiofiles.base

from .dtos import DictState
from .logger import log


class FileHandler(abc.ABC):
    """"""

    @abc.abstractmethod
    def set_files_extension(
        self, nsi_passports: dict[str, str], nsi_dicts: dict[str, dict]
    ) -> tuple[dict[str, str], dict[str, dict]]:
        raise NotImplementedError

    @abc.abstractmethod
    def exists(self, filename: str) -> tuple[str, str]:
        """Проверяет существует ли файл."""
        raise NotImplementedError

    @staticmethod
    @abc.abstractmethod
    def get_records_from_zip_file(dict_state: DictState, zip_buffer: io.BytesIO) -> list[dict]:
        """Считывает записи справочника из файла в архиве."""
        raise NotImplementedError

    @abc.abstractmethod
    async def write_sql_records(self, filename: str, records: str) -> None:
        raise NotImplementedError

    @abc.abstractmethod
    def remove_file(self, filename: str) -> None:
        raise NotImplementedError

    @abc.abstractmethod
    async def read_records(self, filename: str) -> list[dict]:
        """Читает записи справочника из файла."""
        raise NotImplementedError

    @abc.abstractmethod
    async def write_records(self, filename: str, records: list[dict]) -> None:
        """Записывает записи справочника в файл."""
        raise NotImplementedError

    @asynccontextmanager
    @abc.abstractmethod
    async def overwrite_records(self, filename: str, overwrite_from: list[dict] | None = None):
        """
        По умолчанию, перезаписывает файл данными из переменной после ключевого слова `as`
        Или данными из параметра `overwrite_from` если он был передан.
        """
        raise NotImplementedError

    @abc.abstractmethod
    def write_text(self, filename: str, text: str) -> None:
        raise NotImplementedError


class NsiFileHandler(FileHandler):
    """Базовый класс для действий связанных с чтением и записью в файл."""

    def __init__(self, local_path_prefix: str, compress_files: str) -> None:
        self.local_path_prefix = local_path_prefix
        self.compress_files = compress_files

        # TODO(Ars): Сделать enum для типов сжатия
        match self.compress_files:
            case 'gzip':
                self.file_ext = '.gz'
                self.file_open_params = {'mode': 'ab+'}
            case 'compact' | 'readable':
                self.file_ext = ''
                self.file_open_params = {'mode': 'a+', 'encoding': 'utf-8'}

    def set_files_extension(
        self, nsi_passports: dict[str, str], nsi_dicts: dict[str, dict]
    ) -> tuple[dict[str, str], dict[str, dict]]:
        """"""
        nsi_passports['file'] = f'{nsi_passports["file"]}{self.file_ext}'

        for old_key in tuple(nsi_dicts.keys()):
            new_key = f'{old_key}{self.file_ext}'
            nsi_dicts[new_key] = nsi_dicts.pop(old_key)

        return nsi_passports, nsi_dicts

    def _data_packaging(self, data: list[dict]):
        """Запаковывает данные перед сохранением в файл в формат указанный в настройках при запуске."""
        match self.compress_files:
            case 'gzip':
                compact_data = json.dumps(data, ensure_ascii=False, indent=None, separators=(',', ':'))
                return gzip.compress(compact_data.encode('utf-8'))
            case 'compact':
                return json.dumps(data, ensure_ascii=False, indent=None, separators=(',', ':'))
            case 'readable':
                return json.dumps(data, ensure_ascii=False, indent=4)

    def _unpacking_data(self, data, compress_files: str) -> list[dict]:
        """Распаковывает данные из файла в json формат."""
        match compress_files:
            case 'gzip':
                decompressed_data = gzip.decompress(data)
                return json.loads(decompressed_data.decode('utf-8'))
            case 'compact' | 'readable':
                return json.loads(data)

    # TODO(Ars): Сделать enum для первого элемента кортежа
    def exists(self, filename: str) -> tuple[str, str]:
        """Проверяет существует ли файл."""
        exist = os.path.exists(f'{self.local_path_prefix}/{filename}')

        if exist:
            log.debug('file exists: %s', filename)
            return 'exists', filename

        if filename.endswith('.gz'):
            new_filename = filename.replace('.gz', '')
        else:
            new_filename = f'{filename}.gz'

        exist = os.path.exists(f'{self.local_path_prefix}/{new_filename}')
        if exist:
            log.debug('file exists but with another extension: %s', new_filename)
            return 'exists_but_another_ext', new_filename

        log.debug('file not exists: %s', filename)
        return 'not_exists', filename

    @staticmethod
    def get_records_from_zip_file(dict_state: DictState, zip_buffer: io.BytesIO) -> list[dict]:
        """Считывает записи справочника из файла в архиве."""
        log.debug('Чтение: %s', dict_state.dict_filename)
        with zipfile.ZipFile(zip_buffer) as zip_file:
            file_list = zip_file.namelist()
            with zip_file.open(file_list[0]) as json_file:
                return json.load(json_file)['records']

    async def write_sql_records(self, filename: str, records: str) -> None:
        """"""
        log.debug('Запись SQL для: %s', filename)

        filename_with_new_ext = filename.replace('.json', '.sql')
        path_to_file = f'{self.local_path_prefix}/{filename_with_new_ext}'

        match self.compress_files:
            case 'gzip':
                records = gzip.compress(records.encode('utf-8'))

        self.remove_file(filename_with_new_ext)

        async with aiofiles.open(path_to_file, **self.file_open_params) as file:
            await file.write(records)

    def remove_file(self, filename: str) -> None:
        """"""
        try:
            os.remove(f'{self.local_path_prefix}/{filename}')
        except FileNotFoundError:
            pass

    async def read_records(self, filename: str) -> list[dict]:
        """Читает записи справочника из файла."""
        log.debug('Чтение для: %s', filename)

        path_to_file = f'{self.local_path_prefix}/{filename}'

        if filename.endswith('.gz'):
            compress_files = 'gzip'
            file_open_params = {'mode': 'ab+'}
        else:
            compress_files = 'compact'
            file_open_params = {'mode': 'a+', 'encoding': 'utf-8'}

        async with aiofiles.open(path_to_file, **file_open_params) as file:
            await file.seek(0)
            content = await file.read()

        return self._unpacking_data(content, compress_files)

    async def write_records(self, filename: str, records: list[dict]) -> None:
        """Записывает записи справочника в файл."""
        log.debug('Запись для: %s', filename)

        path_to_file = f'{self.local_path_prefix}/{filename}'

        packed_data = self._data_packaging(records)

        async with aiofiles.open(path_to_file, **self.file_open_params) as file:
            await file.write(packed_data)

    @asynccontextmanager
    async def overwrite_records(self, filename: str, overwrite_from: list[dict] | None = None):
        """
        По умолчанию, перезаписывает файл данными из переменной после ключевого слова `as`
        Или данными из параметра `overwrite_from` если он был передан.
        """
        log.debug('Перезапись для: %s', filename)

        path_to_file = f'{self.local_path_prefix}/{filename}'

        async with aiofiles.open(path_to_file, **self.file_open_params) as file:
            #
            await file.seek(0)
            content = await file.read()
            records: list[dict] = self._unpacking_data(content, self.compress_files)

            yield records

            packed_data = self._data_packaging(overwrite_from or records)
            await file.seek(0)
            await file.truncate()  # Полностью очищаем содержимое файла
            await file.write(packed_data)

    def remove_files(self) -> None:
        fixtures_folder = Path(self.local_path_prefix)

        if not fixtures_folder.exists() or not fixtures_folder.is_dir():
            return

        for file_path in fixtures_folder.iterdir():
            if file_path.is_file():  # Удаляем только файлы
                file_path.unlink()
                log.info('Файл был успешно удалён: %r', file_path.as_posix())

    def write_text(self, filename: str, text: str) -> None:
        path_to_file = f'{self.local_path_prefix}/{filename}'
        with open(path_to_file, 'a', encoding='utf-8') as file:
            file.write(text)
