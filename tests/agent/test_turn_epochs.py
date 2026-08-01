"""Tests for the per-session turn epoch registry (turn_epochs.py)."""

from __future__ import annotations

from jenny.agent.turn_epochs import TurnEpochs, TurnToken


class TestTurnEpochs:
    def test_default_epoch_is_zero(self):
        epochs = TurnEpochs()
        assert epochs.current("a:1") == 0

    def test_issue_captures_current_epoch(self):
        epochs = TurnEpochs()
        token = epochs.issue("a:1")
        assert token == TurnToken(key="a:1", epoch=0)
        epochs.bump("a:1")
        assert epochs.issue("a:1").epoch == 1

    def test_bump_repudiates_outstanding_tokens(self):
        epochs = TurnEpochs()
        token = epochs.issue("a:1")
        assert epochs.is_current(token) is True
        assert epochs.bump("a:1") == 1
        assert epochs.is_current(token) is False

    def test_none_token_is_always_current(self):
        epochs = TurnEpochs()
        assert epochs.is_current(None) is True
        epochs.bump("a:1")
        assert epochs.is_current(None) is True

    def test_keys_are_independent(self):
        epochs = TurnEpochs()
        token_a = epochs.issue("a:1")
        token_b = epochs.issue("b:2")
        epochs.bump("a:1")
        assert epochs.is_current(token_a) is False
        assert epochs.is_current(token_b) is True

    def test_readopted_token_survives_bump(self):
        # /new dentro il proprio turno: il token viene ri-adottato al nuovo epoch.
        epochs = TurnEpochs()
        token = epochs.issue("a:1")
        token.epoch = epochs.bump("a:1")
        assert epochs.is_current(token) is True
