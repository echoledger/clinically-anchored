import pytest

from clinically_anchored_api.core import ratelimit


@pytest.fixture(autouse=True)
def _fresh_rate_limits():
    """Rate-limit counters are process-wide; keep tests independent."""
    ratelimit.reset()
    yield
    ratelimit.reset()
