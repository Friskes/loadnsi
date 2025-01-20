import os


def pytest_configure() -> None:
    os.environ['PATH_TO_LOADNSI_CONFIG'] = 'tests/loadnsi_config.py'
    os.environ['NSI_API_USER_KEY'] = 'some-key'
