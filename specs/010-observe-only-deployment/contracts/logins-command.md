# Contract: login command

`python -m trading_agent.storage.logins --service-host HOST[:PORT] [--reset NAME ...]`

Run by the owner on their own machine, with `ADMIN_DATABASE_URL` exported in the shell (the database's public address). It is never run by a service.

## Logins

| Login | Member of | Used by |
|---|---|---|
| `ta_orchestrator_login` | `ta_orchestrator` | orchestrator service |
| `ta_research_login` | `ta_research` | orchestrator service (passed to Research) |
| `ta_portfolio_manager_login` | `ta_portfolio_manager` | orchestrator service (passed to the PM) |
| `ta_risk_gate_login` | `ta_risk_gate` | risk-gate service |
| `ta_reference_data_login` | `ta_reference_data` | reference-data service |
| `ta_execution_login` | `ta_execution` | no service until switch-on |
| `ta_owner_read_login` | `ta_dashboard` (read-only) | the owner, for observation queries |
| `ta_owner_control_login` | `ta_dashboard_control` (`trading_paused` only) | the owner, for the switch-off pause |

Each login is `LOGIN`, `INHERIT` and `NOSUPERUSER NOCREATEDB NOCREATEROLE`, and a member of exactly one group role.

## Behaviour

- Before doing anything, it checks that every group role exists. If one is missing, it refuses with "run migrate first" and exits 2, creating nothing.
- For each login that doesn't exist: it creates the login with a fresh 32-byte random password and prints `NAME  postgresql://NAME:PASSWORD@HOST:PORT/DBNAME` once. `DBNAME` comes from the admin URL, and `HOST:PORT` from `--service-host` (port 5432 if omitted).
- For each login that already exists: it prints `NAME  exists, unchanged`. It doesn't touch the password or memberships.
- `--reset NAME`: it sets a new password for that existing login only, and prints its new string. An unknown name exits 2 with no change.
- It never prints the admin URL or its password, and writes nothing to disk.
- All creations run in one transaction. On any error, nothing is created.
- Exit codes: 0 on success, 2 on a refusal (missing variable, missing group role, unknown `--reset` name, missing `--service-host`), and 3 if the database is unreachable.

## Tests

- **Unit:** argument parsing, connection-string building (including quoting of the password), and that output never contains the admin password.
- **Integration**, against `TEST_DATABASE_URL`:
  - each login exists and connects;
  - each login holds exactly its group's permissions (it can do what its group can, and cannot read a table outside its group's grants);
  - a second run leaves every password unchanged;
  - `--reset` changes only the named login;
  - a missing group role creates nothing.
