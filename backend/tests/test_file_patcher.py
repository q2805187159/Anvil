from __future__ import annotations

import pytest

from anvil.sandbox.file_patcher import apply_patch_operations


def test_apply_patch_operations_supports_anchor_insert_and_text_delete(contract_tmp_path) -> None:
    target = contract_tmp_path / "example.txt"
    target.write_text("alpha\nbeta\ngamma\n", encoding="utf-8")

    result = apply_patch_operations(
        target,
        [
            {"action": "insert_after_anchor", "anchor": "alpha\n", "content": "between\n"},
            {"action": "delete_text", "text": "gamma\n", "expected_old_text": "gamma\n"},
        ],
    )

    assert target.read_text(encoding="utf-8") == "alpha\nbetween\nbeta\n"
    assert result.operations_applied == 2
    assert result.line_count == 3
    assert result.changed is True
    assert result.diff is not None
    assert "+between" in result.diff


def test_apply_patch_operations_supports_dry_run_diff_without_writing(contract_tmp_path) -> None:
    target = contract_tmp_path / "example.txt"
    target.write_text("alpha\nbeta\n", encoding="utf-8")

    result = apply_patch_operations(
        target,
        [{"action": "replace_lines", "start_line": 2, "end_line": 2, "content": "gamma\n"}],
        dry_run=True,
    )

    assert target.read_text(encoding="utf-8") == "alpha\nbeta\n"
    assert result.operations_applied == 1
    assert result.changed is True
    assert result.diff is not None
    assert "-beta" in result.diff
    assert "+gamma" in result.diff


def test_apply_patch_operations_rejects_empty_patch(contract_tmp_path) -> None:
    target = contract_tmp_path / "example.txt"
    target.write_text("alpha\n", encoding="utf-8")

    with pytest.raises(ValueError, match="patch operations must not be empty"):
        apply_patch_operations(target, [])


def test_apply_patch_operations_rejects_missing_anchor(contract_tmp_path) -> None:
    target = contract_tmp_path / "example.txt"
    target.write_text("alpha\n", encoding="utf-8")

    with pytest.raises(ValueError, match="anchor not found"):
        apply_patch_operations(
            target,
            [{"action": "insert_before_anchor", "anchor": "beta", "content": "inserted\n"}],
        )


def test_apply_patch_operations_rejects_expected_old_text_mismatch(contract_tmp_path) -> None:
    target = contract_tmp_path / "example.txt"
    target.write_text("alpha\nbeta\n", encoding="utf-8")

    with pytest.raises(ValueError, match="expected old text mismatch"):
        apply_patch_operations(
            target,
            [
                {
                    "action": "replace_lines",
                    "start_line": 2,
                    "end_line": 2,
                    "content": "gamma\n",
                    "expected_old_text": "delta\n",
                }
            ],
        )


def test_apply_patch_operations_rejects_line_range_out_of_bounds(contract_tmp_path) -> None:
    target = contract_tmp_path / "example.txt"
    target.write_text("alpha\n", encoding="utf-8")

    with pytest.raises(ValueError, match="line range out of bounds"):
        apply_patch_operations(
            target,
            [{"action": "delete_lines", "start_line": 2, "end_line": 3}],
        )
