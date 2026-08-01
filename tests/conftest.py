from __future__ import annotations

from typing import Generator

import pytest

from jenny.config.paths import set_workspace_dir
from jenny.utils.helpers import sync_workspace_templates


@pytest.fixture(scope="session", autouse=True)
def _configure_jenny_workspace(
    tmp_path_factory: pytest.TempPathFactory,
) -> Generator[None, None, None]:
    """Provide a temporary workspace for the entire test suite."""
    data_dir = tmp_path_factory.mktemp("jenny_data")
    workspace = data_dir / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    sync_workspace_templates(workspace, silent=True)

    set_workspace_dir(str(workspace))

    yield

    set_workspace_dir("")
