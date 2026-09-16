import pytest

from goldilocks_agent.cli import main


def test_main_reports_not_implemented(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit):
        main()
    assert "not implemented yet" in capsys.readouterr().err
