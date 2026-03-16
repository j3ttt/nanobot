import importlib.util
from pathlib import Path


def _load_find_meme_module():
    module_path = Path("nanobot/skills/meme/scripts/find_meme.py").resolve()
    spec = importlib.util.spec_from_file_location("meme_find_meme", module_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_find_meme_prefers_exact_or_contains_match(tmp_path) -> None:
    (tmp_path / "[嘲讽].jpg").write_bytes(b"x")
    (tmp_path / "叹气.gif").write_bytes(b"x")

    module = _load_find_meme_module()
    result = module.find_meme("嘲讽", tmp_path)

    assert result is not None
    assert result.endswith("[嘲讽].jpg")


def test_find_meme_returns_none_when_no_match(tmp_path) -> None:
    (tmp_path / "开心.png").write_bytes(b"x")

    module = _load_find_meme_module()
    result = module.find_meme("难过", tmp_path)

    assert result is None
