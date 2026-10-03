from __future__ import annotations

import json
from pathlib import Path

import pytest

from rulelens.dataio import read_records
from rulelens.errors import DataError


def test_csv_with_bom_blank_lines_and_ragged_rows(tmp_path: Path) -> None:
    path = tmp_path / "d.csv"
    path.write_bytes(b"\xef\xbb\xbfa,b\n1,2\n\n3\n4,5,6\n")
    assert list(read_records(path)) == [
        {"a": "1", "b": "2"},
        {"a": "3", "b": None},
        {"a": "4", "b": "5"},
    ]


def test_jsonl_and_ndjson(tmp_path: Path) -> None:
    for name in ("d.jsonl", "d.ndjson"):
        path = tmp_path / name
        path.write_text('{"a": 1}\n\n{"a": {"b": 2}}\n')
        assert list(read_records(path)) == [{"a": 1}, {"a": {"b": 2}}]


def test_json_array(tmp_path: Path) -> None:
    path = tmp_path / "d.json"
    path.write_text(json.dumps([{"a": 1}, {"a": 2}]))
    assert [r["a"] for r in read_records(path)] == [1, 2]


@pytest.mark.parametrize(
    ("name", "content", "fragment"),
    [
        ("d.csv", "", "empty"),
        ("d.jsonl", '{"a": 1}\n{oops\n', "d.jsonl:2: invalid JSON"),
        ("d.jsonl", "[1, 2]\n", "d.jsonl:1: each line must be a JSON object"),
        ("d.json", "{not json", "invalid JSON"),
        ("d.json", '{"a": 1}', "expected a JSON array of objects"),
        ("d.json", "[1, 2]", "expected a JSON array of objects"),
    ],
)
def test_malformed_files(tmp_path: Path, name: str, content: str, fragment: str) -> None:
    path = tmp_path / name
    path.write_text(content)
    with pytest.raises(DataError, match=fragment):
        list(read_records(path))


def test_missing_and_unsupported_files(tmp_path: Path) -> None:
    with pytest.raises(DataError, match="not found"):
        list(read_records(tmp_path / "nope.csv"))
    (tmp_path / "d.xlsx").write_text("x")
    with pytest.raises(DataError, match="unsupported"):
        list(read_records(tmp_path / "d.xlsx"))


def test_undecodable_file(tmp_path: Path) -> None:
    for name in ("d.csv", "d.jsonl", "d.json"):
        (tmp_path / name).write_bytes(b"\xff\xfe\x00bad")
        with pytest.raises(DataError):
            list(read_records(tmp_path / name))
