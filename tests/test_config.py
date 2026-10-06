import os

from app.config import load_dotenv


def test_dotenv_loads_real_values_only(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text('# comment\nBOARDBOT_T1="abc"\nBOARDBOT_T2=sk-ant-...\nBOARDBOT_T3=keepme\n\nBOARDBOT_T4=\n', encoding="utf-8")
    monkeypatch.setenv("BOARDBOT_T3", "already-set")
    for k in ("BOARDBOT_T1", "BOARDBOT_T2", "BOARDBOT_T4"):
        monkeypatch.delenv(k, raising=False)
    load_dotenv(env)
    assert os.environ["BOARDBOT_T1"] == "abc"  # quotes stripped
    assert "BOARDBOT_T2" not in os.environ  # placeholder ignored
    assert os.environ["BOARDBOT_T3"] == "already-set"  # real environment wins
    assert "BOARDBOT_T4" not in os.environ  # empty ignored
    monkeypatch.delenv("BOARDBOT_T1")
