"""
The Smart App Control loader in nlp_service, against simulated refusals.

Windows Smart App Control refuses spaCy's unsigned compiled extensions in
bursts that pass (see backend/services/nlp_service.py and the memory note). A
real refusal cannot be produced on demand, so each scenario runs in a fresh
process with an import hook that raises Windows' exact wording for chosen
modules. Nothing here touches OS security settings, and a "refused" file is
simply never loaded — the same outcome the real policy produces.

Slow by nature: every scenario is a subprocess that loads spaCy and (where
available) LanguageTool, so the module takes a couple of minutes.
"""

import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SENTENCE = "Their going too the libary tomorow"

HEADER = r'''
import importlib.abc, json, sys
sys.path.insert(0, REPO)

class Refuse(importlib.abc.MetaPathFinder):
    """Refuses spacy.* modules by short name, worded exactly as Windows does."""
    def __init__(self, shorts):
        self.shorts, self.hits = set(shorts), 0
    def find_spec(self, fullname, path, target=None):
        short = fullname.rsplit(".", 1)[-1]
        if fullname.startswith("spacy.") and short in self.shorts:
            self.hits += 1
            raise ImportError(f"DLL load failed while importing {short}: "
                              "An Application Control policy has blocked this file.")
        return None

def raises_loudly(fn):
    try:
        fn()
    except RuntimeError as exc:
        return "Smart App Control" in str(exc)
    return False
'''

REPLACEABLE_BODY = r'''
refuse = Refuse(SHORTS)
sys.meta_path.insert(0, refuse)
from backend.services import nlp_service as ns

nlp = ns._get_spacy()
out = {"loaded": nlp is not None, "readiness": ns.checks_readiness()}
out["placeholders"] = sorted(
    short for short, (full, _) in ns._REPLACEABLE_EXTENSIONS.items()
    if full in sys.modules and getattr(sys.modules[full], "__file__", None) is None
)
if nlp is not None:
    from lemminflect import getInflection
    out["lemminflect"] = list(getInflection("go", tag="VBD"))
    out["issue_types"] = sorted({i["type"] for i in ns.check_text(SENTENCE)["issues"]})
    from spacy.matcher import Matcher
    matcher = Matcher(nlp.vocab)
    matcher.add("THE", [[{"LOWER": "the"}]])
    out["plain_matcher_works"] = len(matcher(nlp("the cat"))) == 1
    import spacy
    if "dependencymatcher" in SHORTS:
        out["dependencymatcher_raises"] = raises_loudly(
            lambda: spacy.matcher.DependencyMatcher(nlp.vocab))
    if "levenshtein" in SHORTS:
        lev = sys.modules["spacy.matcher.levenshtein"]
        out["levenshtein_raises"] = raises_loudly(lambda: lev.levenshtein_compare("a", "b"))
        out["factory_returns_raising_compare"] = raises_loudly(
            lambda: lev.make_levenshtein_compare()("a", "b"))
    if "edit_trees" in SHORTS:
        et = sys.modules["spacy.pipeline._edit_tree_internals.edit_trees"]
        out["edit_trees_raises"] = raises_loudly(lambda: et.EditTrees(nlp.vocab.strings))
print(json.dumps(out))
'''

USED_BINARY_BODY = r'''
refuse = Refuse({"strings"})      # the StringStore: the pipeline cannot run without it
sys.meta_path.insert(0, refuse)
from backend.services import nlp_service as ns

first = ns._get_spacy()
hits = refuse.hits
second = ns._get_spacy()          # inside the cooldown: must not try again
out = {
    "first_load_failed": first is None,
    "readiness_while_refused": ns.checks_readiness(),
    "reason_names_strings": "strings" in (ns.checks_status()[1] or ""),
    "no_retry_inside_cooldown": second is None and refuse.hits == hits,
    "check_text_degrades": ns.check_text(SENTENCE)["checks_available"] is False,
}
sys.meta_path.remove(refuse)      # the burst passes
ns._SPACY_RETRY_SECONDS = 0       # skip the wait rather than sleeping it out
nlp = ns._get_spacy()
out["recovered"] = nlp is not None
out["readiness_after"] = ns.checks_readiness()
out["real_module_used"] = getattr(sys.modules.get("spacy.strings"), "__file__", None) is not None
if nlp is not None:
    from lemminflect import getInflection
    out["lemminflect"] = list(getInflection("go", tag="VBD"))
    out["issue_types"] = sorted({i["type"] for i in ns.check_text(SENTENCE)["issues"]})
print(json.dumps(out))
'''


def run_scenario(body: str, shorts: list[str] | None = None) -> dict:
    preamble = (
        f"REPO = {str(REPO_ROOT)!r}\n"
        f"SENTENCE = {SENTENCE!r}\n"
        f"SHORTS = {sorted(shorts or [])!r}\n"
    )
    proc = subprocess.run(
        [sys.executable, "-c", preamble + HEADER + body],
        capture_output=True, text=True, cwd=REPO_ROOT, timeout=900,
    )
    lines = [line for line in proc.stdout.splitlines() if line.startswith("{")]
    assert lines, f"scenario produced no result\nstdout:\n{proc.stdout[-2000:]}\nstderr:\n{proc.stderr[-2000:]}"
    return json.loads(lines[-1])


@pytest.mark.parametrize(
    "shorts",
    [
        pytest.param([], id="nothing-refused"),
        pytest.param(["edit_trees"], id="edit_trees"),
        pytest.param(["dependencymatcher"], id="dependencymatcher"),
        pytest.param(["levenshtein"], id="levenshtein"),
        pytest.param(["dependencymatcher", "edit_trees", "levenshtein"], id="all-three"),
    ],
)
def test_unused_extensions_are_replaced_and_spacy_still_loads(shorts):
    result = run_scenario(REPLACEABLE_BODY, shorts)

    assert result["loaded"] and result["readiness"] == "ready"
    # Exactly the refused modules got placeholders, and nothing else did.
    assert result["placeholders"] == sorted(shorts)
    assert result["lemminflect"] == ["went"], "the sys.modules purge must stay"
    assert {"spelling", "homophone"} <= set(result["issue_types"])
    assert result["plain_matcher_works"] is True

    # Whatever was replaced must fail loudly if something genuinely uses it.
    for key in ("dependencymatcher_raises", "levenshtein_raises",
                "factory_returns_raising_compare", "edit_trees_raises"):
        if key in result:
            assert result[key] is True, key


def test_a_refused_module_the_pipeline_needs_recovers_after_the_cooldown():
    """spacy/strings was refused on 2026-09-10, and nothing can stand in for it.

    The old code recorded that failure for the life of the process, so the
    checks stayed down until a restart even after the burst passed.
    """
    result = run_scenario(USED_BINARY_BODY)

    assert result["first_load_failed"]
    assert result["readiness_while_refused"] == "unavailable"
    assert result["reason_names_strings"]
    assert result["no_retry_inside_cooldown"], "a failed load must be trusted briefly"
    assert result["check_text_degrades"], "the notepad keeps working without checks"

    assert result["recovered"] and result["readiness_after"] == "ready"
    assert result["real_module_used"], "recovery must use the real module, not a placeholder"
    assert result["lemminflect"] == ["went"]
    assert {"spelling", "homophone"} <= set(result["issue_types"])
