from app.config import Settings
from app.wiring import build_engine


def test_wikiloc_cookie_setting_turns_on_login_mode(tmp_path, monkeypatch):
    monkeypatch.delenv("WIKILOC_COOKIE", raising=False)
    anon = build_engine(Settings(_env_file=None, cache_dir=tmp_path))
    assert anon.wikiloc_login is False
    logged = build_engine(Settings(_env_file=None, cache_dir=tmp_path, wikiloc_cookie="SESSION=abc"))
    assert logged.wikiloc_login is True and "abc" not in repr(logged.wikiloc)
    blank = build_engine(Settings(_env_file=None, cache_dir=tmp_path, wikiloc_cookie=""))
    assert blank.wikiloc_login is False


def test_cookie_stays_out_of_settings_repr():
    s = Settings(_env_file=None, wikiloc_cookie="SESSION=SECRETX")
    assert "SECRETX" not in repr(s) and "SECRETX" not in str(s)
