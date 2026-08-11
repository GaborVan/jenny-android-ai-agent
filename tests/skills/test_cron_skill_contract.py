"""La skill `cron` è il documento che raggiunge davvero il dispositivo.

Le skill vengono ri-estratte a OGNI avvio senza ``skip_existing``
(``jenny/utils/helpers.py``), mentre ``AGENTS.md`` è in
``_USER_OWNED_TEMPLATES`` con ``skip_existing=True`` — quindi una regola di
comportamento scritta solo lì non arriva mai su un'installazione esistente.
Il pacchetto dice inoltre all'agente di leggere le skill *prima* di schedulare.

Questa asimmetria è la ragione per cui il difetto è sopravvissuto: la skill
descriveva ancora "Three Modes" (una tassonomia che precede il parametro ``mode``)
e portava come esempio l'anti-pattern esatto, mentre la guida corretta viveva in un
file che sul telefono dell'utente non è mai stato aggiornato.
"""

from __future__ import annotations

import re
from pathlib import Path

from jenny.agent.tools.cron import _JOB_MODES
from jenny.utils.android_assets import _SKILLS_MANIFEST, _USER_OWNED_TEMPLATES

SKILL = Path(__file__).resolve().parents[2] / "jenny" / "skills" / "cron" / "SKILL.md"


def _skill() -> str:
    return SKILL.read_text(encoding="utf-8")


def test_the_skill_is_shipped() -> None:
    assert "cron/SKILL.md" in _SKILLS_MANIFEST


def test_it_documents_every_real_mode() -> None:
    text = _skill()
    for mode in _JOB_MODES:
        assert f'mode="{mode}"' in text, f"la skill non nomina mode={mode!r}"


def test_the_stale_taxonomy_is_gone() -> None:
    """"Three Modes: Reminder / Task / One-time" precede il parametro ``mode``.

    Finché era lì, l'agente leggeva che il parametro non esisteva.
    """
    text = _skill()
    assert "Three Modes" not in text
    assert not re.search(r"^\s*2\.\s*\*\*Task\*\*", text, re.M)


def test_it_does_not_teach_the_anti_pattern() -> None:
    """L'esempio storico era ``message="Check GitHub stars and report"`` *senza*
    ``mode``: un controllo ricorrente creato come reminder, che poi parla a ogni
    ciclo. Se un esempio ricorrente resta senza ``mode``, torna il difetto."""
    for block in re.findall(r"cron\(action=\"add\"[^)]*\)", _skill(), re.S):
        if "every_seconds" in block or "cron_expr" in block:
            assert "mode=" in block, f"esempio ricorrente senza mode: {block}"


def test_it_says_that_a_conditional_request_is_a_monitor() -> None:
    # Whitespace normalizzato: l'asserzione è sul contenuto, non su dove il
    # markdown va a capo.
    text = re.sub(r"\s+", " ", _skill().lower())
    assert "conditional" in text
    assert "only tell me if" in text
    assert "any request phrased as a condition is a `monitor`" in text


def test_it_forbids_the_filler_the_user_actually_received() -> None:
    text = _skill()
    for filler in ("All clear.", "All done.", "nothing to report"):
        assert filler in text, f"la skill non nomina il riempitivo {filler!r}"
    assert "never send filler" in text.lower()


def test_it_calls_silence_a_success() -> None:
    text = _skill().lower()
    assert "silence is a **correct, successful outcome**" in _skill()
    assert "silenced" in text, "action='list' riporta 'silenced': va spiegato"


def test_it_covers_the_heartbeat_alternative() -> None:
    """Il caso reale dell'utente era una riga di HEARTBEAT.md, non un cron job."""
    text = _skill()
    assert "HEARTBEAT.md" in text
    assert "message" in text


def test_agents_md_is_not_the_only_home_of_the_rule() -> None:
    """Guardia sull'asimmetria: ``AGENTS.md`` non si aggiorna, la skill sì."""
    assert "AGENTS.md" in _USER_OWNED_TEMPLATES
    # La regola operativa (scegliere il mode) deve esistere nella skill, che è
    # l'unico dei due a raggiungere un'installazione già esistente.
    assert 'mode="monitor"' in _skill()
