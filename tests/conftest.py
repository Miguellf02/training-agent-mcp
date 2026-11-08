import logfire
import pytest


@pytest.fixture(autouse=True, scope="session")
def _logfire_offline() -> None:
    logfire.configure(send_to_logfire=False, console=False, inspect_arguments=False)


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"
