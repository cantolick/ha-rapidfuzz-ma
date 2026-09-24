"""Guards a Home Assistant template pitfall the service tests can't see.

The blueprint gates music_assistant.play_media on a template. HA template
conditions can't coerce a dict to a boolean, so gating on `{{ res.media }}`
(a dict) evaluated false even for a confident match and the play step never
ran. The gate must be an explicit boolean expression.
"""
from pathlib import Path

import yaml

BLUEPRINT = Path(__file__).resolve().parents[2] / "blueprints" / "audiobook_voice_handler.yaml"


class _Loader(yaml.SafeLoader):
    pass


_Loader.add_constructor("!input", lambda loader, node: {"!input": loader.construct_scalar(node)})


def _walk(node):
    if isinstance(node, dict):
        yield node
        for value in node.values():
            yield from _walk(value)
    elif isinstance(node, list):
        for value in node:
            yield from _walk(value)


def play_media_guards(path=BLUEPRINT):
    blueprint = yaml.load(path.read_text(), Loader=_Loader)
    return [
        node["if"]
        for node in _walk(blueprint["action"])
        if "then" in node
        and any(
            isinstance(step, dict) and step.get("action") == "music_assistant.play_media"
            for step in node["then"]
        )
    ]


def test_play_media_is_guarded_by_an_explicit_boolean():
    guards = play_media_guards()
    assert guards, "expected an if/then wrapping music_assistant.play_media"
    for guard in guards:
        assert "is mapping" in guard, f"guard must be an explicit boolean test, got: {guard}"
