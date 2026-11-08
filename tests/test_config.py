"""Paths resolve against the project root, wherever the process was started."""

from pathlib import Path

import pytest

from coach.config import PROJECT_ROOT, Settings
from coach.models import GoalConfig

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def elsewhere(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Start every test outside the repo, with no COACH_* paths inherited."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("COACH_GOAL_PATH", raising=False)
    monkeypatch.delenv("COACH_FIXTURES_PATH", raising=False)


def test_project_root_is_the_repo() -> None:
    assert PROJECT_ROOT == ROOT


def test_default_goal_path_is_absolute() -> None:
    settings = Settings(_env_file=None)  # type: ignore[call-arg]
    assert settings.goal_path == ROOT / "data/goal.json"


def test_relative_paths_from_the_environment_are_anchored_to_the_root(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("COACH_FIXTURES_PATH", "evals/some.json")
    monkeypatch.setenv("COACH_GOAL_PATH", "data/other.json")
    settings = Settings(_env_file=None)  # type: ignore[call-arg]
    assert settings.fixtures_path == ROOT / "evals/some.json"
    assert settings.goal_path == ROOT / "data/other.json"


def test_absolute_paths_are_kept(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    absolute = tmp_path / "activities.json"
    monkeypatch.setenv("COACH_FIXTURES_PATH", str(absolute))
    settings = Settings(_env_file=None)  # type: ignore[call-arg]
    assert settings.fixtures_path == absolute


def test_synthetic_goal_can_be_selected_from_the_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("COACH_GOAL_PATH", "evals/synthetic/goal.json")
    settings = Settings(_env_file=None)  # type: ignore[call-arg]
    assert settings.goal_path == ROOT / "evals/synthetic/goal.json"
    assert GoalConfig.model_validate_json(settings.goal_path.read_text(encoding="utf-8"))


def test_anthropic_key_is_read_from_the_env_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    env_file = tmp_path / ".env"
    env_file.write_text("ANTHROPIC_API_KEY=fake-key-for-tests\n", encoding="utf-8")
    settings = Settings(_env_file=env_file)  # type: ignore[call-arg]
    assert settings.anthropic_api_key is not None
    assert settings.anthropic_api_key.get_secret_value() == "fake-key-for-tests"


def test_env_file_does_not_depend_on_the_working_directory() -> None:
    env_file = Settings.model_config.get("env_file")
    assert env_file == ROOT / ".env"


def test_empty_values_in_the_env_file_count_as_unset(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # .env.example ships "ANTHROPIC_API_KEY=": left empty it must mean "no key", not a blank key.
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    env_file = tmp_path / ".env"
    env_file.write_text("ANTHROPIC_API_KEY=\nCOACH_INTERVALS_API_KEY=\n", encoding="utf-8")
    settings = Settings(_env_file=env_file)  # type: ignore[call-arg]
    assert settings.anthropic_api_key is None
    assert settings.intervals_api_key is None
