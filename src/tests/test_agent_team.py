"""Contrat de l'équipe d'agents Claude Code (.claude/agents) décrite dans CLAUDE.md."""

import re
from pathlib import Path

import pytest
import yaml  # PyYAML, installé avec langchain

ROOT = Path(__file__).resolve().parents[2]
AGENTS = sorted((ROOT / ".claude" / "agents").glob("*.md"))
CLAUDE_MD = (ROOT / "CLAUDE.md").read_text(encoding="utf-8")

# Skills qui définissent chaque rôle : les retirer change le métier de l'agent.
REQUIRED_SKILLS = {
    "dermato-architecte": {"ponytail", "graphify"},
    "dermato-ingenieur-rag": {"ponytail", "test-driven-development"},
    "dermato-generation-llm": {"ponytail", "omni-combos-routing"},
    "dermato-curateur-corpus": {"pubmed-recherche-pdf", "zotero-bibliotheque"},
    "dermato-validateur-clinique": {"uptodate-recherche"},
    "dermato-evaluateur-rag": {"ponytail", "test-driven-development"},
    "dermato-relecteur": {"code-review-and-quality", "security-and-hardening"},
}
SECRET = re.compile(r"AIza[0-9A-Za-z_\-]{35}|sk-[A-Za-z0-9_\-]{20,}|-----BEGIN [A-Z ]*PRIVATE KEY")


def frontmatter(path: Path) -> dict:
    # Claude Code ignore sans erreur un agent au YAML invalide (ex. « audit : revue ») :
    # on parse avec un vrai parseur YAML.
    text = path.read_text(encoding="utf-8")
    assert text.startswith("---\n"), f"{path.name}: le frontmatter doit commencer ligne 1"
    return yaml.safe_load(text.split("---\n", 2)[1])


def test_roster_matches_claude_md():
    defined = {p.stem for p in AGENTS}
    documented = set(re.findall(r"^\| `(dermato-[a-z-]+)` \|", CLAUDE_MD, re.M))
    assert defined == documented == set(REQUIRED_SKILLS)


@pytest.mark.parametrize("path", AGENTS, ids=lambda p: p.stem)
def test_agent_definition(path):
    fm = frontmatter(path)
    assert fm["name"] == path.stem
    assert len(fm["description"]) > 80, "la description guide la délégation : la préciser"
    assert fm.get("model") == "inherit", "pas de choix de modèle automatique (règle OmniRoute)"
    assert REQUIRED_SKILLS[path.stem] <= set(fm.get("skills", []))
    assert not SECRET.search(path.read_text(encoding="utf-8"))


def test_reviewer_is_read_only():
    fm = frontmatter(ROOT / ".claude" / "agents" / "dermato-relecteur.md")
    denied = {tool.strip() for tool in fm["disallowedTools"].split(",")}
    assert {"Write", "Edit", "NotebookEdit"} <= denied


def test_uptodate_is_never_a_corpus_source():
    for name in ("dermato-curateur-corpus", "dermato-validateur-clinique"):
        body = (ROOT / ".claude" / "agents" / f"{name}.md").read_text(encoding="utf-8")
        assert re.search(r"(?i)n'ing[ée]rer ni stocker aucun contenu uptodate|"
                         r"ne jamais copier de texte uptodate", body), name
    assert not SECRET.search(CLAUDE_MD)
