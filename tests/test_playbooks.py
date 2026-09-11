"""FR-44: playbooks are data; a missing file is a warning, not a crash."""
import logging

from sveta import playbooks


def test_trip_trigger_and_attach():
    assert playbooks.matching("E-ticket LY315 TLV-BER El Al") == ["trip"]
    assert playbooks.matching("Бронирование отеля подтверждено") == ["trip"]
    assert playbooks.matching("Re: отчёт за квартал") == []
    block = playbooks.attach("Ваш посадочный талон")
    assert block.startswith("\n\nPlaybook «trip» applies") and "suggest" in block


def test_missing_playbook_is_skipped_with_a_warning(monkeypatch, caplog):
    monkeypatch.setitem(playbooks.TRIGGERS, "ghost", playbooks.TRIGGERS["trip"])
    with caplog.at_level(logging.WARNING):
        block = playbooks.attach("билет")
    assert "Playbook «trip»" in block and "Playbook «ghost»" not in block
    assert "playbook ghost: file missing" in caplog.text
