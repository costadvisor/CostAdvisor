"""Scrum 28 — complex formula ops in the safe AST evaluator.

Covers the new function calls (min/max/abs/round/clamp/step), conditional /
threshold logic (ternary, comparisons, chained, boolean), and confirms the
whitelist still blocks code injection (no builtins, calls, or attribute access
outside the whitelist).
"""
import pytest

from app.services.costing_engine import safe_eval_expr as ev

CTX = {"ACN": 120.0, "AA": 80.0, "h": 0.3, "FC": 50.0}


def test_baseline_arithmetic_unchanged():
    assert ev("0.92*[(0.75*ACN+1500)*(1-h)+h*AA/0.8]+FC", CTX) == pytest.approx(1101.56)


def test_min_max_abs_round():
    assert ev("max(ACN, AA)", CTX) == 120.0
    assert ev("min(ACN, AA)", CTX) == 80.0
    assert ev("abs(AA - ACN)", CTX) == 40.0
    assert ev("round(h * 10)", CTX) == 3.0


def test_clamp_bounds():
    assert ev("clamp(ACN, 0, 100)", CTX) == 100.0     # above upper bound
    assert ev("clamp(AA, 90, 200)", CTX) == 90.0      # below lower bound
    assert ev("clamp(AA, 0, 200)", CTX) == 80.0       # within bounds


def test_step_function():
    assert ev("step(ACN, 100, 0, 1)", CTX) == 1.0     # 120 >= 100 -> above
    assert ev("step(AA, 100, 0, 1)", CTX) == 0.0      # 80 < 100 -> below


def test_conditional_and_thresholds():
    assert ev("ACN if ACN < 100 else 100", CTX) == 100.0
    assert ev("1 if 0 < h < 1 else 0", CTX) == 1.0     # chained comparison
    assert ev("AA if (h > 0.5) else ACN", CTX) == 120.0
    assert ev("1 if (h > 0 and ACN > 100) else 0", CTX) == 1.0


def test_mod_operator():
    assert ev("ACN % 100", CTX) == 20.0


@pytest.mark.parametrize("bad", [
    '__import__("os")',
    "ACN.__class__",
    'open("x")',
    'eval("1")',
    "unknownfn(ACN)",
])
def test_injection_and_unknown_calls_blocked(bad):
    with pytest.raises(ValueError):
        ev(bad, CTX)


def test_undefined_variable_raises():
    with pytest.raises(ValueError):
        ev("NOPE + 1", CTX)
