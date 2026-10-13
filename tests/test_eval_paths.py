"""The evaluator uses adopted repository labels from any working directory."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from eval.run_eval import gold_path_for  # noqa: E402


def test_default_gold_is_the_adopted_repository_file_from_any_cwd(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    path = gold_path_for("record_1.pdf", None)
    assert path == Path(__file__).resolve().parents[1] / "eval/gold/record_1.json"
    gold = json.loads(path.read_text())
    assert gold["labeled_pages"] == [1, 188]
    assert len(gold["records"]) == 31
    # Derek explicitly adopted one record here, rejecting the earlier draft split.
    spans = [(r["start_page"], r["end_page"]) for r in gold["records"]]
    assert (37, 41) in spans
    assert not any(start == 40 for start, _ in spans)


def test_explicit_gold_override_still_selects_a_separate_draft(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    draft = tmp_path / "draft.json"
    draft.write_text('{"filename": "custom.pdf", "records": []}')
    assert gold_path_for("record_1.pdf", str(draft)) == draft
    assert gold_path_for("record_1.pdf", "draft.json").read_text() == draft.read_text()
