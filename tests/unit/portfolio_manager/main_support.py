"""Harness for the entry point's provider and dry-run tests: `main()` with fakes injected."""

from __future__ import annotations

from dataclasses import dataclass, field

from tests.fakes.model import FakeModel
from tests.fakes.pm_store import FakePmStore
from tests.unit.portfolio_manager.builders import inputs, report
from tests.unit.portfolio_manager.service_support import Clock, answer_of, decide, market
from trading_agent.portfolio_manager import __main__ as runner


class FakeConn:
    def __init__(self):
        self.closed = False

    def close(self):
        self.closed = True


@dataclass
class Result:
    code: int
    store: FakePmStore
    model: FakeModel
    seen: dict = field(default_factory=dict)
    required: list = field(default_factory=list)


def run_main(
    args=(),
    *,
    clock=None,
    config_path=None,
    answer=None,
    quotes=None,
    reports=None,
    monkeypatch=None,
) -> Result:
    """`monkeypatch` is needed to record which variables `main` asks for by name."""
    clock = clock or Clock()
    quotes = quotes if quotes is not None else market(("AAPL", "200"))
    store = FakePmStore(inputs(reports or [report("db-aapl", "AAPL")]))
    model = FakeModel(answer if answer is not None else answer_of(decide()))
    result = Result(0, store, model)

    if monkeypatch is not None:
        real = runner.require_env

        def spy(name):
            result.required.append(name)
            return real(name)

        monkeypatch.setattr(runner, "require_env", spy)

    def model_factory(provider, key, settings, *, base_url=None):
        result.seen["model"] = (provider, key, base_url)
        return model

    kwargs = {"config_path": config_path} if config_path is not None else {}
    result.code = runner.main(
        list(args),
        quotes_factory=lambda key: quotes,
        model_factory=model_factory,
        connect=lambda url, **kw: result.seen.setdefault("conn", FakeConn()),
        store_factory=lambda conn: store,
        clock=clock,
        sleep=clock.sleep,
        **kwargs,
    )
    return result
