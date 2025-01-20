import pytest
from click.testing import CliRunner
from src.loadnsi.loadnsi import loadnsi


@pytest.mark.skip(reason='TODO')
def test_loadnsi():
    runner = CliRunner()
    result = runner.invoke(
        loadnsi,
        # loadnsi --log_level=DEBUG --compress_files=gzip --use_pirate_api --show_model_data=file --model_data_params=all_fields_not_required --forced_update organization_nsi department_nsi  # noqa: E501
        # [
        #     '--log_level', 'DEBUG',
        #     '--compress_files', 'gzip',
        #     '--use_pirate_api',  # Флаговая опция (без значения)
        #     '--show_model_data', 'file',
        #     '--model_data_params', 'all_fields_not_required',
        #     '--forced_update', 'organization_nsi', 'department_nsi',  # Последовательные значения
        # ],
    )
    print(result)
    # print(result.exit_code)
    # print(result.exception)
    # print(result.output)
