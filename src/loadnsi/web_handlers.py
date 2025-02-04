import abc
import io
import json
import logging

import httpx
from bs4 import BeautifulSoup
from tenacity import (
    RetryCallState,
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,  # noqa: F401
    wait_random,
)

from .console_view import (
    rich_track_download,
    rich_track_retry_wait,
    rich_track_timeout,
)
from .dtos import DictState
from .exceptions import (
    NsiBadResultError,
    NsiFileNotFoundError,
    NsiScriptTagNotFoundError,
)
from .logger import log

MAX_ATTEMPT_NUMBER = 4


class SafeDictFormatMap(dict):
    """Всегда возвращает исходный ключ форматирования `...{key}...` для пропущенных ключей."""

    def __missing__(self, key) -> str:
        return f'{{{key}}}'


def parse_build_id_from_html(text: str) -> str:
    soup = BeautifulSoup(text, 'html.parser')
    # Ищем элемент: <script id="__NEXT_DATA__" type="application/json">
    script_tag = soup.find('script', id='__NEXT_DATA__', type='application/json')
    if not script_tag:
        raise NsiScriptTagNotFoundError(
            'Тег: <script id="__NEXT_DATA__" type="application/json"> не найден.'
        )
    # Извлекаем текстовое содержимое тега
    tag_data: dict = json.loads(script_tag.string)
    return tag_data.get('buildId')


async def handle_retry_error_callback(retry_state: RetryCallState) -> None:
    """"""
    log.debug(f'{retry_state}; args: {retry_state.args[1:]};')
    # Завершение контекста если retry исчерпал все попытки (handle_before не срабатывает)
    await retry_state.rich_track_retry_wait_ctx.__aexit__(None, None, None)
    # Проброс последнего исключения наверх, чтобы его поймал gather и отменил задачу
    raise retry_state.outcome.exception()


async def handle_before_sleep(retry_state: RetryCallState) -> None:
    """"""
    log.debug(f'{retry_state}; args: {retry_state.args[1:]};')


async def handle_before(retry_state: RetryCallState) -> None:
    """"""
    # if retry_state.idle_for == 0.0:
    # if retry_state.upcoming_sleep == 0.0:
    if retry_state.attempt_number == 1:
        # Пропускаем первый вызов пустышку, хз зачем он сделан.
        return
    await retry_state.rich_track_retry_wait_ctx.__aexit__(None, None, None)


async def handle_after(retry_state: RetryCallState) -> None:
    """"""
    dict_state: DictState = retry_state.args[1]
    description = (
        f'Сон №{retry_state.attempt_number}/{MAX_ATTEMPT_NUMBER} '
        f'перед попыткой повторного запроса: {dict_state.dict_filename}'
    )
    retry_state.rich_track_retry_wait_ctx = rich_track_retry_wait(
        description,
        retry_state.upcoming_sleep,  # retry_state.idle_for
    )
    await retry_state.rich_track_retry_wait_ctx.__aenter__()


# NOTE(Ars): На каждый метод должен использоваться свой экземпляр декоратора @retry
retry_params = dict(
    # Повтор только при конкретных исключениях
    retry=retry_if_exception_type((httpx.HTTPError, NsiBadResultError)),
    stop=stop_after_attempt(MAX_ATTEMPT_NUMBER),  # Делаем 4 попытки
    wait=wait_random(min=3, max=6),  # Ждем от 3 до 6 секунд перед следующей попыткой
    # wait=tenacity.wait_fixed(5),  # Ждем ровно N секунд перед следующей попыткой
    # wait=wait_exponential(multiplier=0.2, max=10),  # Ждём 0.2 -> 0.4 -> 0.8 -> 1.6 -> 3.2 и т.д.
    retry_error_callback=handle_retry_error_callback,  # Вызывается перед wait последней попытки
    before_sleep=handle_before_sleep,  # Вызывается перед wait каждой попытки кроме последней
    before=handle_before,
    after=handle_after,
)


class NsiWebCrawler(abc.ABC):
    """Базовый класс для действий связанных с загрузкой данных из внешней системы по HTTP."""

    def __init__(
        self,
        nsi_base_url: str,
        nsi_versions_path: str,
        nsi_passport_path: str,
        nsi_files_path: str,
    ) -> None:
        self.nsi_versions_path = nsi_versions_path
        self.nsi_passport_path = nsi_passport_path
        self.nsi_files_path = nsi_files_path

        self.data_client_kw = {
            'params': {},
            'headers': {
                'user-agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
                '(KHTML, like Gecko) Chrome/106.0.0.0 Safari/537.36',
            },
            'verify': False,
            'timeout': httpx.Timeout(35),
            'follow_redirects': True,
            'base_url': nsi_base_url,
        }

        self.file_client_kw = self.data_client_kw.copy()

    @abc.abstractmethod
    async def get_remote_passport(self, dict_state: DictState, get_kwargs: dict | None = None) -> dict:
        """Возвращает паспорт справочника."""
        log.debug('Запрос паспорта для: %s', dict_state.dict_filename)
        async with httpx.AsyncClient(**self.data_client_kw) as client:
            #
            description = f'Ожидание ответа на получение паспорта: {dict_state.dict_filename}'
            async with rich_track_timeout(description, client):
                response: httpx.Response = await client.get(**get_kwargs)

            log.debug('status_code: %s', response.status_code)
            response.raise_for_status()
            return response.json()

    @abc.abstractmethod
    async def get_dict_versions(self, dict_state: DictState) -> dict:
        """Возвращает информацию о версиях справочника."""
        log.debug('Запрос для: %s', dict_state.dict_filename)
        async with httpx.AsyncClient(**self.data_client_kw) as client:
            #
            description = f'Ожидание ответа на получение версий: {dict_state.dict_filename}'
            async with rich_track_timeout(description, client):
                response = await client.get(
                    self.nsi_versions_path, params={'identifier': dict_state.oid}
                )

            log.debug('status_code: %s', response.status_code)
            response.raise_for_status()
            return response.json()

    @retry(**retry_params)
    async def get_zip_filename(self, dict_state: DictState, version: str, format: str = 'JSON') -> str:  # noqa: A002
        """Формирует название zip архива."""
        log.debug('Запрос на получение названия архива для: %s', dict_state.dict_filename)
        async with httpx.AsyncClient(**self.file_client_kw) as client:
            #
            description = f'Ожидание ответа на получение названия архива: {dict_state.dict_filename}'
            async with rich_track_timeout(description, client):
                response = await client.get(
                    self.nsi_files_path,
                    params={'identifier': dict_state.oid, 'version': version, 'format': format},
                )

            log.debug('status_code: %s', response.status_code)
            response.raise_for_status()

            data = response.json()
            if not data:
                raise NsiFileNotFoundError(
                    f'Файл с версией: {version} не найден, '
                    'возможно он ещё не создан в системе NSI по крону.'
                )
            return data[0]

    @retry(**retry_params)
    async def download_zip(self, dict_state: DictState, zip_filename: str) -> io.BytesIO:
        """Скачивает zip архив в потоковом режиме с отображением прогресса."""
        log.debug('Скачивание для: %s Архива: %s', dict_state.dict_filename, zip_filename)
        zip_buffer = io.BytesIO()

        async with httpx.AsyncClient(**self.file_client_kw) as client:
            request = client.build_request('GET', f'{self.nsi_files_path}/{zip_filename}')

            description = f'Ожидание ответа на получение файла архива: {dict_state.dict_filename}'
            async with rich_track_timeout(description, client):
                response = await client.send(request, stream=True)

            log.debug('status_code: %s', response.status_code)
            response.raise_for_status()

            async with rich_track_download(dict_state, response):
                async for chunk in response.aiter_bytes():
                    zip_buffer.write(chunk)

            await response.aclose()

        return zip_buffer


class OfficialApiNsiWebCrawler(NsiWebCrawler):
    """
    Класс для действий связанных с загрузкой данных из внешней системы по HTTP.
    Работающий с официальным апи НСИ который использует аутентификацию по токену.

    https://nsi.rosminzdrav.ru/port/swagger-ui.html
    """

    def __init__(
        self,
        nsi_base_url: str,
        nsi_versions_path: str,
        nsi_passport_path: str,
        nsi_files_path: str,
        nsi_api_user_key: str | None = None,
    ) -> None:
        super().__init__(nsi_base_url, nsi_versions_path, nsi_passport_path, nsi_files_path)

        if nsi_api_user_key:
            self.data_client_kw['params'].update({'userKey': nsi_api_user_key})

    @retry(**retry_params)
    async def get_remote_passport(self, dict_state: DictState, get_kwargs: dict | None = None) -> dict:
        get_kwargs = {'url': self.nsi_passport_path, 'params': {'identifier': dict_state.oid}}
        data = await super().get_remote_passport(dict_state, get_kwargs)
        self._raise_for_result(data)
        return data

    @retry(**retry_params)
    async def get_dict_versions(self, dict_state: DictState) -> dict:
        data = await super().get_dict_versions(dict_state)
        self._raise_for_result(data)
        return data

    @staticmethod
    def _raise_for_result(data: dict) -> None:
        # {'result': 'ERROR', 'resultText': 'Запрашиваемая версия не существует', 'resultCode': '03x0006'}  # noqa: E501
        if data['result'] != 'OK':
            raise NsiBadResultError(f'{data!r}')


class PirateApiNsiWebCrawler(NsiWebCrawler):
    """
    Класс для действий связанных с загрузкой данных из внешней системы по HTTP.
    Работающий с пиратским апи НСИ для которого не требуеться аутентификация.
    """

    def __init__(
        self,
        nsi_base_url: str,
        nsi_versions_path: str,
        nsi_passport_path: str,
        nsi_files_path: str,
    ) -> None:
        super().__init__(nsi_base_url, nsi_versions_path, nsi_passport_path, nsi_files_path)
        try:
            resp_text = self._get_html_with_build_id()
        except Exception as exc:
            if log.level == logging.DEBUG:
                raise exc
            raise type(exc)('Не удалось получить buildId, попробуйте повторить попытку.')  # noqa: B904

        build_id = parse_build_id_from_html(resp_text)

        self.nsi_passport_path = self.nsi_passport_path.format_map(SafeDictFormatMap(buildId=build_id))

    @retry(
        retry=retry_if_exception_type((httpx.HTTPError,)),
        stop=stop_after_attempt(MAX_ATTEMPT_NUMBER),
        wait=wait_random(min=3, max=6),
    )
    def _get_html_with_build_id(self) -> str:
        """Возвращает buildId для построения url path к апи получения passport."""
        log.debug('Запрос для получения buildId')
        data_client_kw = dict(self.data_client_kw, timeout=httpx.Timeout(15))
        with httpx.Client(**data_client_kw) as client:
            response = client.get('')
            log.debug('status_code: %s', response.status_code)
            response.raise_for_status()
        return response.text

    @retry(**retry_params)
    async def get_remote_passport(self, dict_state: DictState, get_kwargs: dict | None = None) -> dict:
        get_kwargs = {'url': self.nsi_passport_path.format(identifier=dict_state.oid, version='latest')}
        data = await super().get_remote_passport(dict_state, get_kwargs)
        return data['pageProps']['dict']

    @retry(**retry_params)
    async def get_dict_versions(self, dict_state: DictState) -> dict:
        return await super().get_dict_versions(dict_state)
