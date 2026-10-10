"""Trusted semantic acceptance, against the explicit human prefix decision."""

import json
import os
import sys
from pathlib import Path


def verify(decision_path):
    # The protected operator file is created only from an actual human answer.
    decision = json.loads(Path(decision_path).read_text())
    assert decision["source"] == "USER_EXPLICIT_F37_PREFIX"
    prefix = decision["prefix"]
    assert isinstance(prefix, str) and prefix.strip() and "\x00" not in prefix
    sys.path.insert(0, os.getcwd())
    from delta import greet_delta
    from epsilon import even_sum
    from zeta import signature

    expected = prefix + ", Ada!"
    assert greet_delta("  Ada  ") == expected
    for value in (None, 3, True, "", "   "):
        try:
            greet_delta(value)
        except (TypeError, ValueError):
            pass
        else:
            raise AssertionError("delta must reject invalid/empty input")
    assert even_sum([2, 4, -2, 3]) == 4 and even_sum([]) == 0
    for value in ([True], [2.0], ["2"], None):
        try:
            even_sum(value)
        except (TypeError, ValueError):
            pass
        else:
            raise AssertionError("epsilon must reject noninteger/bool input")
    assert signature(" Ada ") == expected + " [F37]"
    readme = Path("README.md").read_text()
    assert "greet_delta" in readme and "Ada" in readme and "```python" in readme
    # Run the documented bounded function example as trusted code, never arbitrary Markdown.
    assert greet_delta("Ada") == expected
    assert all(
        Path("tests", name).is_file()
        for name in ("test_delta.py", "test_epsilon.py", "test_zeta.py")
    )


if __name__ == "__main__":
    verify(sys.argv[1])
