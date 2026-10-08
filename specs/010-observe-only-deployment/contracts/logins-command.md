# Contract: login command

`python -m trading_agent.storage.logins --service-host HOST[:PORT] [--reset NAME ...]`

Run by the owner on their own machine, with `ADMIN_DATABASE_URL` exported in the shell (the database's public address). It is never run by a service.

## Logins

| Login | Member of | Used by |
|---|---|---|
| `ta_orchestrator_login` | `ta_orchestrator` | orchestrator service |
| `ta_research_login` | `ta_research` | orchestrator service (passed to Research) |
| `ta_opportunistic_identifier_login` | `ta_opportunistic_identifier` | orchestrator service (passed to the Opportunistic Identifier) |
| `ta_portfolio_manager_login` | `ta_portfolio_manager` | orchestrator service (passed to the PM) |
| `ta_risk_gate_login` | `ta_risk_gate` | risk-gate service |
| `ta_reference_data_login` | `ta_reference_data` | reference-data service |
| `ta_execution_login` | `ta_execution` | execution service |
| `ta_owner_read_login` | `ta_dashboard` (read-only) | the owner, for observation queries |
| `ta_owner_control_login` | `ta_dashboard_control` (`trading_paused` only) | the owner: sets the pause during setup, clears it at switch-on |

Each login is `LOGIN`, `INHERIT` and `NOSUPERUSER NOCREATEDB NOCREATEROLE`, and a member of exactly one group role.

## Behaviour

- Before doing anything, it checks that every group role exists. If one is missing, it refuses with "run migrate first" and exits 2, creating nothing.
- For each login that doesn't exist: it creates the login with a fresh 32-byte random password and prints `NAME  postgresql://NAME:PASSWORD@HOST:PORT/DBNAME` once. `DBNAME` comes from the admin URL, and `HOST:PORT` from `--service-host` (port 5432 if omitted).
- The password reaches the database as a SCRAM-SHA-256 verifier computed by the driver (`pgconn.encrypt_password`), so the plain password is never in a statement or the server log. The printed string still holds the plain password.
- Before changing anything, it checks every login that already exists: it must be `NOSUPERUSER NOCREATEDB NOCREATEROLE` and a member of exactly its row's group role. On any mismatch it refuses, naming the login, with exit 2 and no change. The same check runs before `--reset`.
- For each login that already exists: it prints `NAME  exists, unchanged`. It doesn't touch the password or memberships.
- `--reset NAME`: it sets a new password for that existing login only, and prints its new string. An unknown name exits 2 with no change, and so does a name that doesn't exist in the database yet. Repeating a name is the same as giving it once.
- It never prints the admin URL or its password, and writes nothing to disk.
- All creations run in one transaction. On any error, nothing is created.
- Exit codes: 0 on success, 2 on a refusal (missing variable, missing group role, an existing login that differs from the contract, unknown `--reset` name, missing `--service-host`), 3 if the database is unreachable, and 1 for any other error. Both 1 and 3 print only the exception type and "re-run to check; use --reset if a login shows as existing": a failure can land after the transaction began, or after it committed but before the reply arrived, so the run may or may not have created logins.

## Tests

- **Unit:** argument parsing, connection-string building (including quoting of the password), and that output never contains the admin password.
- **Integration**, against `TEST_DATABASE_URL`:
  - each login exists and connects;
  - each login holds exactly its group's permissions (it can do what its group can, and cannot read a table outside its group's grants);
  - a second run leaves every password unchanged;
  - the stored password is a SCRAM verifier, and an existing login with an extra privilege or a second group is refused with nothing changed;
  - `--reset` changes only the named login;
  - a missing group role creates nothing.

## Amendment notes

**Amended 2026-10-07 by feature 011** ([`specs/011-opportunistic-identifier`](../../011-opportunistic-identifier/spec.md), FR-022). The table has nine rows, not eight: `ta_opportunistic_identifier_login` is a member of `ta_opportunistic_identifier`, whose role and grants already exist (migrations 0001 and 0002), and is passed to the Opportunistic Identifier by the orchestrator. Everything else in this contract is unchanged.
