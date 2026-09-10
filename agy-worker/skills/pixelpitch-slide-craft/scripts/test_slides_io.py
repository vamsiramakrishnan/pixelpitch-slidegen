# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Every shape the gates have to accept, including the one that used to crash.

The index-of-file-references case is the deliverable the deck skill's workspace
reference tells authors to write, and passing it to the lint raised an
AttributeError until this reader existed. It is the first test here for that
reason.
"""

from __future__ import annotations

import json

import pytest
from slides_io import load_slides


def write(tmp_path, name, text):
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return path


def test_index_of_file_references_resolves_against_its_own_directory(tmp_path):
    deck = tmp_path / "slides"
    deck.mkdir()
    write(deck, "01-open.html", "<p>open</p>")
    write(deck, "02-close.html", "<p>close</p>")
    index = write(
        deck,
        "index.json",
        json.dumps(
            {
                "slides": [
                    {"file": "01-open.html", "title": "Open", "role": "hero"},
                    {"file": "02-close.html", "title": "Close", "role": "closing"},
                ]
            }
        ),
    )

    slides = load_slides(index)

    assert [s["html"] for s in slides] == ["<p>open</p>", "<p>close</p>"]
    assert slides[0]["title"] == "Open"
    assert slides[1]["role"] == "closing"


def test_directory_of_html_sorts_by_filename(tmp_path):
    write(tmp_path, "02-second.html", "<p>b</p>")
    write(tmp_path, "01-first.html", "<p>a</p>")
    write(tmp_path, "notes.txt", "ignored")

    slides = load_slides(tmp_path)

    assert [s["html"] for s in slides] == ["<p>a</p>", "<p>b</p>"]
    assert [s["file"] for s in slides] == ["01-first.html", "02-second.html"]


def test_inline_array(tmp_path):
    path = write(tmp_path, "s.json", json.dumps([{"html": "<p>x</p>"}]))
    assert load_slides(path) == [{"html": "<p>x</p>"}]


def test_wrapped_inline_array(tmp_path):
    path = write(tmp_path, "s.json", json.dumps({"slides": [{"html": "<p>x</p>"}]}))
    assert load_slides(path) == [{"html": "<p>x</p>"}]


def test_html_wins_over_a_stale_file_reference(tmp_path):
    write(tmp_path, "old.html", "<p>stale</p>")
    path = write(
        tmp_path, "s.json", json.dumps([{"file": "old.html", "html": "<p>fresh</p>"}])
    )
    assert load_slides(path)[0]["html"] == "<p>fresh</p>"


@pytest.mark.parametrize(
    "payload, expected",
    [
        ([], "non-empty"),
        ({"slides": []}, "non-empty"),
        (["<p>bare string</p>"], "not an object"),
        ([{"title": "no body"}], "neither 'html' nor 'file'"),
        ([{"file": "gone.html"}], "missing"),
    ],
)
def test_bad_input_names_the_problem(tmp_path, payload, expected):
    path = write(tmp_path, "s.json", json.dumps(payload))
    with pytest.raises(SystemExit) as raised:
        load_slides(path)
    assert expected in str(raised.value)


def test_empty_directory_is_an_error_not_an_empty_deck(tmp_path):
    with pytest.raises(SystemExit) as raised:
        load_slides(tmp_path)
    assert "no .html files" in str(raised.value)
