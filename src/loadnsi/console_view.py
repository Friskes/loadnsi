import shutil
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx
from rich.console import Console, Group
from rich.live import Live
from rich.logging import RichHandler
from rich.panel import Panel
from rich.progress import (
    BarColumn,
    DownloadColumn,
    Progress,
    SpinnerColumn,
    TextColumn,
    TransferSpeedColumn,
)
from rich.text import Text

from .dtos import DictState

rich_console = Console()


def update_console_size() -> None:
    """
    Обновляет размеры консоли, чтобы убрать дублирование элементов
    при изменении размера терминала.
    """
    _width, _height = shutil.get_terminal_size()
    rich_console.size = (_width - 1, _height - 4)


log_handler = RichHandler(
    # level=options['log_level'],
    console=rich_console,
    show_time=False,
    # show_level=False,
    show_path=False,
    rich_tracebacks=True,
)

download_progress_bar = Progress(
    TextColumn('[cyan][progress.description]{task.description}'),
    BarColumn(bar_width=None),
    TextColumn('[progress.percentage]{task.percentage:>3.0f}%'),
    DownloadColumn(),
    TransferSpeedColumn(),
)
timeout_progress_bar = Progress(
    SpinnerColumn(),
    TextColumn('[cyan][progress.description]{task.description}'),
)
retry_wait_progress_bar = Progress(
    SpinnerColumn(),
    TextColumn('[cyan][progress.description]{task.description}'),
)

rich_panel1 = Panel(
    retry_wait_progress_bar, title='Сон перед повторной попыткой', border_style='yellow', expand=True
)
rich_panel2 = Panel(
    timeout_progress_bar, title='Ожидание ответа от сервера', border_style='blue', expand=True
)

download_text = Text()
download_group = Group(download_text, download_progress_bar)
rich_panel3 = Panel(download_group, title='Загрузка справочников', border_style='green', expand=True)

rich_group = Group(rich_panel1, rich_panel2, rich_panel3)

rich_live = Live(rich_group, console=rich_console)


@asynccontextmanager
async def rich_track_download(dict_state: DictState, response: httpx.Response):
    """
    Контекстный менеджер для отслеживания прогресса загрузки с помощью rich.
    """
    description = f'Загрузка справочника: {dict_state.dict_filename}'

    task_id = download_progress_bar.add_task(
        description=f'[white]№{0:2}:[/white] {description}',
        total=int(response.headers.get('Content-Length', 0)),
    )

    if not download_text._text[0]:
        download_text._text[0] = f'Всего справочников: {dict_state.total_dicts}шт.'

    original_aiter_bytes = response.aiter_bytes

    async def aiter_bytes_with_progress(chunk_size: int | None = None) -> AsyncIterator[bytes]:
        async for chunk in original_aiter_bytes(chunk_size):
            yield chunk
            download_progress_bar.update(
                task_id,
                description=(f'[white]№{task_id:2}:[/white] {description}'),
                completed=response.num_bytes_downloaded,
                # advance=len(chunk),
            )
            update_console_size()  # XXX(Ars): Надеюсь это не замедлит загрузку..

    response.aiter_bytes = aiter_bytes_with_progress
    try:
        yield task_id
    finally:
        response.aiter_bytes = original_aiter_bytes


@asynccontextmanager
async def rich_track_timeout(description: str, client: httpx.AsyncClient):
    """"""
    task_id = timeout_progress_bar.add_task(
        description=f'{description} до {client.timeout.connect}сек.',
    )
    try:
        yield task_id
    finally:
        timeout_progress_bar.remove_task(task_id)


@asynccontextmanager
async def rich_track_retry_wait(description: str, total: float):
    """"""
    task_id = retry_wait_progress_bar.add_task(
        description=f'{description} {total:.1f}сек.',
    )
    try:
        yield task_id
    finally:
        retry_wait_progress_bar.remove_task(task_id)
