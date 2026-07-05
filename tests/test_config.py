"""Tests for the zero-dependency .env loader."""
import os

from super_menu.core.config import load_dotenv


def test_load_dotenv_parses_and_populates(tmp_path):
    env = tmp_path / ".env"
    env.write_text(
        "# a comment\n"
        "\n"
        "ORS_API_KEY = abc123 \n"
        'export QUOTED="hello world"\n'
        "SINGLE='x'\n"
        "NOT_A_PAIR\n",
        encoding="utf-8",
    )
    keys = ("ORS_API_KEY", "QUOTED", "SINGLE", "NOT_A_PAIR")
    saved = {k: os.environ.pop(k, None) for k in keys}
    try:
        loaded = load_dotenv(env)
        assert loaded == {"ORS_API_KEY": "abc123", "QUOTED": "hello world", "SINGLE": "x"}
        assert os.environ["ORS_API_KEY"] == "abc123"
        assert os.environ["QUOTED"] == "hello world"
        assert "NOT_A_PAIR" not in os.environ
    finally:  # load_dotenv mutates the real os.environ — restore it for other tests
        for k in keys:
            os.environ.pop(k, None)
            if saved[k] is not None:
                os.environ[k] = saved[k]


def test_existing_env_var_wins(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text("ORS_API_KEY=from-file\n", encoding="utf-8")
    monkeypatch.setenv("ORS_API_KEY", "from-shell")
    load_dotenv(env)
    assert os.environ["ORS_API_KEY"] == "from-shell"  # a real export is not clobbered


def test_missing_file_is_ok(tmp_path):
    assert load_dotenv(tmp_path / "nope.env") == {}


if __name__ == "__main__":
    import tempfile
    from pathlib import Path

    # minimal fixture-free run for the smoke-style entrypoint
    d = Path(tempfile.mkdtemp())
    (d / ".env").write_text("SUPER_MENU_TEST_KEY=ok\n", encoding="utf-8")
    os.environ.pop("SUPER_MENU_TEST_KEY", None)
    assert load_dotenv(d / ".env") == {"SUPER_MENU_TEST_KEY": "ok"}
    assert load_dotenv(d / "missing.env") == {}
    print("config tests passed")
