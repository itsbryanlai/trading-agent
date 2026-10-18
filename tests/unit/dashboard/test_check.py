"""`--check` (specs/014 tasks T025; research D16; contracts/dashboard-interface.md)."""

from __future__ import annotations

import base64

import pytest

from tests.fakes.dashboard_control import FakeControl
from tests.fakes.dashboard_store import GOOD_LOGIN, FakeStore, failing
from trading_agent.dashboard import __main__ as runner
from trading_agent.dashboard import auth, check
from trading_agent.dashboard.control import ControlError
from trading_agent.dashboard.deployed import DEFAULT_CONFIG_DIR

GOOD_HASH = auth.hash_password("correct horse battery staple", salt=b"0123456789abcdef")
GOOD_SECRET = "fake-secret-not-real-0123456789abcdef0123"
READ = "ta_dashboard_login"
CONTROL = "ta_dashboard_control_login"
ORDER = [
    "database_read",
    "database_control",
    "password_hash",
    "session_secret",
    "schedule",
    "config",
]


def login(name, **flags):
    return {**GOOD_LOGIN, "login": name, **flags}


def run(*, store=None, control=None, password_hash=GOOD_HASH, secret=GOOD_SECRET, config_dir=None):
    return check.run(
        store=store or FakeStore(),
        control=control or FakeControl(),
        password_hash=password_hash,
        secret=secret,
        config_dir=config_dir or DEFAULT_CONFIG_DIR,
    )


def lines(results):
    return [r.line() for r in results]


# --- everything ok --------------------------------------------------------------------------


def test_six_lines_in_order_all_ok():
    results = run()
    assert [r.name for r in results] == ORDER
    assert lines(results) == [f"{name}: ok" for name in ORDER]
    assert all(r.ok for r in results)


def test_nothing_is_written():
    control = FakeControl()
    store = FakeStore()
    run(store=store, control=control)
    assert control.calls == []  # set_paused never called
    assert set(store.names()) <= {"ping", "memberships"}


# --- the role rules -------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("info", "roles", "code"),
    [
        (login("ta_owner_read_login"), ["ta_dashboard"], "wrong_login"),
        (login("ta_assistant_login"), ["ta_dashboard"], "wrong_login"),
        (login(READ, rolsuper=True), ["ta_dashboard"], "privileged"),
        (login(READ, rolcreaterole=True), ["ta_dashboard"], "privileged"),
        (login(READ, rolcreatedb=True), ["ta_dashboard"], "privileged"),
        (login(READ, rolbypassrls=True), ["ta_dashboard"], "privileged"),
        (login(READ), [], "wrong_role:ta_dashboard"),
        (login(READ), ["ta_assistant"], "wrong_role:ta_dashboard"),
        (login(READ), ["ta_dashboard", "ta_dashboard_control"], "extra_role:ta_dashboard_control"),
        (login(READ), ["ta_assistant", "ta_dashboard", "ta_risk_gate"], "extra_role:ta_assistant"),
        (login(READ), ["pg_write_all_data", "ta_dashboard"], "extra_role:pg_write_all_data"),
        (login(READ), ["postgres", "ta_dashboard"], "extra_role:postgres"),
        (login(READ), ["ta_dashboard", "trading_owner"], "extra_role:trading_owner"),
    ],
)
def test_the_read_login_rules(info, roles, code):
    results = run(store=FakeStore(memberships=(info, roles)))
    assert results[0].line() == f"database_read: FAILED {code}"
    assert all(r.ok for r in results[1:])


@pytest.mark.parametrize(
    ("info", "roles", "code"),
    [
        (login("ta_owner_control_login"), ["ta_dashboard_control"], "wrong_login"),
        (login(CONTROL, rolcreaterole=True), ["ta_dashboard_control"], "privileged"),
        (login(CONTROL), ["ta_dashboard"], "wrong_role:ta_dashboard_control"),
        (login(CONTROL), ["ta_dashboard_control", "ta_dashboard"], "extra_role:ta_dashboard"),
        (
            login(CONTROL),
            ["pg_write_all_data", "ta_dashboard_control"],
            "extra_role:pg_write_all_data",
        ),
    ],
)
def test_the_control_login_rules(info, roles, code):
    results = run(control=FakeControl(memberships=(info, roles)))
    assert results[1].line() == f"database_control: FAILED {code}"
    assert results[0].ok


def test_the_wrong_name_is_reported_before_privilege_and_roles():
    both = login("someone_else", rolsuper=True)
    results = run(store=FakeStore(memberships=(both, ["ta_assistant"])))
    assert results[0].code == "wrong_login"


def test_each_login_is_checked_against_its_own_name_and_role():
    swapped = run(
        store=FakeStore(memberships=(login(CONTROL), ["ta_dashboard_control"])),
        control=FakeControl(memberships=(login(READ), ["ta_dashboard"])),
    )
    assert swapped[0].code == "wrong_login" and swapped[1].code == "wrong_login"


def test_an_unreachable_database_is_unreachable_and_the_rest_still_run():
    results = run(
        store=failing(), control=FakeControl(membership_error=ControlError("database: X"))
    )
    assert results[0].line() == "database_read: FAILED unreachable"
    assert results[1].line() == "database_control: FAILED unreachable"
    assert all(r.ok for r in results[2:])


# --- the password hash ----------------------------------------------------------------------


def _hand_hash(n=32768, r=8, p=1, salt=b"0123456789abcdef", digest=b"d" * 32):
    def enc(raw: bytes) -> str:
        return base64.b64encode(raw).decode()

    return f"scrypt${n}${r}${p}${enc(salt)}${enc(digest)}"


def test_only_the_exact_production_cost_passes():
    assert check.password_hash_code(GOOD_HASH) is None
    assert check.password_hash_code(_hand_hash()) is None


@pytest.mark.parametrize(
    "value",
    [
        _hand_hash(n=16384),
        _hand_hash(n=65536),
        _hand_hash(n=2**20),
        _hand_hash(r=4),
        _hand_hash(r=32),
        _hand_hash(p=2),
        _hand_hash(digest=b"d" * 31),
        _hand_hash(digest=b"d" * 64),
        "scrypt$32768$8$1$AAAA$AAAA",
        "",
        "not a hash",
        "bcrypt$32768$8$1$AAAA$AAAA",
    ],
)
def test_any_other_cost_or_shape_is_malformed(value):
    assert check.password_hash_code(value) == "malformed"
    results = run(password_hash=value)
    assert results[2].line() == "password_hash: FAILED malformed"


# --- the session secret ---------------------------------------------------------------------


@pytest.mark.parametrize(
    ("secret", "code"), [("x" * 32, None), ("x" * 31, "too_short"), ("", "too_short")]
)
def test_the_secret_needs_32_bytes(secret, code):
    assert check.session_secret_code(secret) == code


def test_bytes_not_characters_are_counted():
    assert check.session_secret_code("é" * 16) is None  # 32 bytes
    assert check.session_secret_code("é" * 15) == "too_short"


# --- schedule and config --------------------------------------------------------------------


def test_an_unreadable_schedule_fails(tmp_path):
    (tmp_path / "schedule.yaml").write_text("portfolio_manager:\n  run_while_paused: maybe\n")
    results = run(config_dir=tmp_path)
    assert results[4].line() == "schedule: FAILED unreadable"
    (tmp_path / "schedule.yaml").write_text("portfolio_manager:\n  run_while_paused: false\n")
    assert run(config_dir=tmp_path)[4].ok


def test_a_bad_config_file_fails(monkeypatch):
    def broken():
        raise check.DashboardConfigError("page_size: bad")

    monkeypatch.setattr(check, "load_config", broken)
    assert run()[5].line() == "config: FAILED malformed"


# --- no value is ever printed ---------------------------------------------------------------


def test_no_failure_line_holds_a_value_or_message():
    secret_hash = "MARKER-HASH-VALUE"
    results = run(
        store=FakeStore(memberships=(login("MARKER-LOGIN"), ["MARKER-ROLE"])),
        password_hash=secret_hash,
        secret="MARKER-SHORT",
    )
    text = "\n".join(lines(results))
    assert "MARKER" not in text


# --- the command ----------------------------------------------------------------------------

ENV = {
    "DASHBOARD_DATABASE_URL": "postgresql://reader:MARKER-DB-PASSWORD@db.example/prod",
    "DASHBOARD_CONTROL_DATABASE_URL": "postgresql://writer:MARKER-CTRL-PASSWORD@db.example/prod",
    "DASHBOARD_PASSWORD_HASH": GOOD_HASH,
    "DASHBOARD_SESSION_SECRET": GOOD_SECRET,
}


def _patch_run(monkeypatch, results):
    monkeypatch.setattr(runner.check, "run", lambda **kwargs: results)


def test_exit_0_only_when_every_check_is_ok(monkeypatch, capsys):
    _patch_run(monkeypatch, [check.Result(name, None) for name in ORDER])
    assert runner.main(["--check"], ENV) == 0
    assert capsys.readouterr().out.splitlines() == [f"{name}: ok" for name in ORDER]


def test_exit_1_when_any_check_fails_and_every_line_is_still_printed(monkeypatch, capsys):
    results = [check.Result(name, None) for name in ORDER]
    results[1] = check.Result("database_control", "wrong_login")
    _patch_run(monkeypatch, results)
    assert runner.main(["--check"], ENV) == 1
    out = capsys.readouterr().out.splitlines()
    assert len(out) == 6 and out[1] == "database_control: FAILED wrong_login"


@pytest.mark.parametrize("name", sorted(ENV))
def test_a_missing_variable_exits_2_naming_it_and_runs_nothing(name, monkeypatch, capsys):
    called = []
    monkeypatch.setattr(runner.check, "run", lambda **kwargs: called.append(1))
    environ = {k: v for k, v in ENV.items() if k != name}
    assert runner.main(["--check"], environ) == 2
    captured = capsys.readouterr()
    assert name in captured.err and "MARKER" not in captured.err + captured.out
    assert called == [] and captured.out == ""


def test_check_needs_no_port(monkeypatch):
    _patch_run(monkeypatch, [check.Result(name, None) for name in ORDER])
    assert "PORT" not in ENV
    assert runner.main(["--check"], ENV) == 0


def test_check_builds_the_real_store_and_writer_from_the_variables(monkeypatch):
    seen = {}

    def fake_run(**kwargs):
        seen.update(kwargs)
        return [check.Result(name, None) for name in ORDER]

    monkeypatch.setattr(runner.check, "run", fake_run)
    runner.main(["--check"], ENV)
    assert isinstance(seen["store"], runner.PostgresStore)
    assert isinstance(seen["control"], runner.ControlWriter)
    assert seen["password_hash"] == GOOD_HASH and seen["secret"] == GOOD_SECRET


def test_the_membership_queries_list_every_role_but_the_login_itself():
    from trading_agent.dashboard import control, store

    for query in (store.GROUP_ROLES, control.GROUP_ROLES):
        text = " ".join(query.split())
        assert "pg_has_role(current_user, oid, 'MEMBER')" in text
        assert "rolname <> current_user" in text
        assert "LIKE" not in text  # not only ta_* roles
