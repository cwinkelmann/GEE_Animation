"""scripts/check_notebooks.py: tracked notebooks carry no outputs, no execution
counts, and never set the operator's EE project in a code cell."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import check_notebooks as C  # noqa: E402


def _nb(cells):
    return {"nbformat": 4, "nbformat_minor": 5, "metadata": {}, "cells": cells}


def _code(source, outputs=None, count=None):
    return {"cell_type": "code", "source": [source], "metadata": {},
            "outputs": outputs or [], "execution_count": count}


def _write(tmp_path, name, nb):
    p = tmp_path / name
    p.write_text(json.dumps(nb), encoding="utf-8")
    return str(p)


def test_a_clean_notebook_has_no_problems(tmp_path):
    p = _write(tmp_path, "clean.ipynb", _nb([
        {"cell_type": "markdown", "source": ["EE_PROJECT=hnee-331218 is the operator's"], "metadata": {}},
        _code("print('hi')")]))
    assert C.problems(p) == []
    assert C.main([p]) == 0


def test_flags_stored_outputs_and_execution_counts(tmp_path):
    p = _write(tmp_path, "dirty.ipynb", _nb([
        _code("1+1", outputs=[{"output_type": "execute_result"}], count=3)]))
    msgs = C.problems(p)
    assert any("stored outputs" in m for m in msgs)
    assert any("execution_count" in m for m in msgs)
    assert C.main([p]) == 1


def test_flags_operator_project_in_code_but_not_markdown(tmp_path):
    p = _write(tmp_path, "proj.ipynb", _nb([
        {"cell_type": "markdown", "source": ["hnee-331218"], "metadata": {}},
        _code('PROJECT = "hnee-331218"')]))
    assert [m for m in C.problems(p) if "hnee-331218" in m] != []


def test_unparseable_notebook_is_a_problem_not_a_crash(tmp_path):
    p = tmp_path / "broken.ipynb"
    p.write_text("{not json", encoding="utf-8")
    assert C.main([str(p)]) == 1
