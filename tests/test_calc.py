import re

import pytest
from conftest import fake_web, load

from remotesearch.commands.calc import CalcError, calc, calculate, convert


@pytest.mark.parametrize(
    ("expression", "value"),
    [
        ("2+2*3", 8),
        ("(3+4)^2", 49),
        ("15% of 80", 12),
        ("3 x 4", 12),
        ("1,000*3", 3000),
        ("10/3", 10 / 3),
        ("2**0.5", 2**0.5),
        ("-5^2", -25),
        ("sqrt(16) + abs(-2)", 6),
        ("sin(30)", 0.5),
        ("log(1000)", 3),
        ("round(2.6)", 3),
        ("2 * pi", 6.283185307179586),
    ],
)
def test_calculate(expression: str, value: float) -> None:
    assert calculate(expression) == pytest.approx(value)


@pytest.mark.parametrize(
    ("expression", "why"),
    [
        ("9**9**9", "that power is too big to work out"),
        ("99**999", "the answer is out of range"),
        ("1/0", "it can't divide by zero"),
        ("sqrt(-1)", "the answer is out of range"),
        ("(1+2", "that isn't an expression it can read"),
        ("1" * 101, "that's too long to work out"),
        ("__import__('os').system('ls')", "it only does numbers"),
        ("().__class__", "it only does numbers"),
        ("x = 1", "that isn't an expression it can read"),
        ("[1, 2]", "it only does numbers"),
        ("true + 1", "it only does numbers"),
        ("sqrt(4, 2)", "it only does numbers"),
    ],
)
def test_calculate_refuses_anything_but_arithmetic(expression: str, why: str) -> None:
    with pytest.raises(CalcError, match=re.escape(why)):
        calculate(expression)


def test_calc_reply() -> None:
    assert calc("15% of 80") == "15% of 80 = 12"
    assert calc("10/3") == "10/3 = 3.333333333"
    assert calc("2^20") == "2^20 = 1,048,576"
    assert calc("1/0") == "Couldn't work out '1/0', as it can't divide by zero."


@pytest.mark.parametrize(
    ("text", "reply"),
    [
        ("10 km to mi", "10 km = 6.2137 mi"),
        ("5 feet in metres", "5 feet = 1.524 metres"),
        ("8 fl oz to ml", "8 fl oz = 236.5882 ml"),
        ("100 kph to mph", "100 kph = 62.1371 mph"),
        ("10 in to cm", "10 in = 25.4 cm"),
        ("3 Kg to lbs", "3 Kg = 6.6139 lbs"),
        ("1,500 m to km", "1,500 m = 1.5 km"),
        ("70 f to c", "70F = 21.11C"),
        ("-40 c to f", "-40C = -40F"),
        ("300 kelvin to degrees celsius", "300K = 26.85C"),
        ("10 km to kg", "Can't convert length to weight."),
    ],
)
def test_convert_units(text: str, reply: str) -> None:
    with fake_web({}) as calls:
        assert convert(text) == reply
    assert calls == []


def test_convert_currency_reads_the_recorded_rate() -> None:
    with fake_web({"https://api.frankfurter.dev/": load("frankfurter_usd_cad.json")}) as calls:
        assert convert("50 usd to cad") == "50 USD = 70.72 CAD (ECB rate for 2026-09-25)"
    assert calls == [
        (
            "https://api.frankfurter.dev/v1/latest",
            {"base": "USD", "symbols": "CAD", "amount": 50.0},
        )
    ]


def test_convert_an_unknown_currency_or_something_else_entirely() -> None:
    # Frankfurter answers an unknown code with a 404, which comes through as None.
    with fake_web({}):
        assert convert("50 usd to xyz") == (
            "Frankfurter has no rate from USD to XYZ. It covers about 30 currencies from the "
            "European Central Bank."
        )
        assert convert("pdf to word") is None
        assert convert("5 parsecs to furlongs") is None
