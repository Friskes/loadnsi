import asyncio
import uuid
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field


# TODO(Ars): Отделить поля паспорта от полей справочника
@dataclass
class PassportState:
    pass


@dataclass
class DictState:
    dict_filename: str
    model: str  # dict model
    oid: str  # passport oid
    total_dicts: int
    passport_semaphore: asyncio.Semaphore
    passport_name: str = ''
    passport_pk: str = field(default_factory=lambda: str(uuid.uuid4()))
    dict_model_data: dict[str, dict] = field(default_factory=dict)
    filter_func: Callable[[dict], bool] | None = None
    fields: Iterable[str] | None = None
    version_changed: bool = False
    create_sql: bool = False
    dict_changed: bool = False
    forced_update: bool = False

    def __post_init__(self):
        if self.filter_func is not None and not callable(self.filter_func):
            raise TypeError('Объект filter должен быть вызываемым - Callable')

    def __repr__(self) -> str:
        return f'<DictState(dict_filename={self.dict_filename})>'
