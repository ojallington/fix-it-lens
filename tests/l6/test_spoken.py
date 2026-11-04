import json
from pathlib import Path

import pytest

from eval.spoken import has_markdown, main, sentences

MARKED = ["Turn it **clockwise**.", "Turn it *clockwise* now.", "- Lift the filter out.", "1. Lift the filter.",
          "> Reinig de zeven.", "## Filters", "Set it to `H05`.", "| dH | setting |\n|---|---|",
          "See [page 25](p25)."]
PLAIN = ["Turn the filter clockwise until the arrows meet.", "Set the hardness to H05, for 17 to 21 °dH.",
         "It is 2*3 = 6 litres.", "Your last clean was on 10 August [entry 6].", "Zet de waarde op H05."]


@pytest.mark.parametrize("text", MARKED)
def test_markdown_is_caught(text: str) -> None:
    assert has_markdown(text)


@pytest.mark.parametrize("text", PLAIN)
def test_plain_speech_passes(text: str) -> None:
    assert not has_markdown(text)


def test_scan_counts_server_sentences_only_and_never_prints_text(tmp_path: Path,
                                                                  capsys: pytest.CaptureFixture[str]) -> None:
    s = tmp_path / "abc"
    s.mkdir()
    lines = [{"dir": "s2c", "msg": {"type": "sentence", "id": "s1", "text": "Lift it **out**."}},
             {"dir": "s2c", "msg": {"type": "sentence", "id": "s2", "text": "Lift it out."}},
             {"dir": "c2s", "msg": {"type": "sentence", "id": "x", "text": "**not ours**"}},
             {"dir": "s2c", "msg": {"type": "notice", "code": "lookup_failed"}}]
    (s / "events.jsonl").write_text("".join(json.dumps(x) + "\n" for x in lines))
    assert [i for _, i, _ in sentences(tmp_path)] == ["s1", "s2"]
    assert main(["--sessions", str(tmp_path)]) == 1
    out, err = capsys.readouterr()
    assert json.loads(out)["value"] == 1 and json.loads(out)["n"] == 2
    assert "abc sentence s1" in err and "out" not in err.replace("markdown", "")
