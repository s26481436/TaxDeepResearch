"""A run must say which code it is.

`git log` showed the merge commit while the import still resolved to an older
non-editable install, so the prompt version on disk and the one actually used
disagreed with no way to see it from the output.
"""

from __future__ import annotations

import inspect

import taxwatch.cli as cli
from taxwatch.requirements.prompts import PROMPT_VERSION


def test_provenance_names_the_prompt_version_and_the_package(capsys):
    cli._echo_provenance()
    out = capsys.readouterr().out
    assert PROMPT_VERSION in out
    assert "taxwatch" in out.lower(), "the path is the half that resolves a stale install"


def test_extract_requirements_reports_provenance():
    assert "_echo_provenance()" in inspect.getsource(cli.extract_requirements)
