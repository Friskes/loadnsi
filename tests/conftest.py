import os

from dotenv import find_dotenv, load_dotenv

load_dotenv(find_dotenv())


def pytest_configure() -> None:
    os.environ['PATH_TO_LOADNSI_CONFIG'] = os.environ.get(
        'PATH_TO_LOADNSI_CONFIG', 'tests/loadnsi_config.py'
    )
    os.environ['NSI_API_USER_KEY'] = os.environ.get('NSI_API_USER_KEY', 'some-key')
