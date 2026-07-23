# Third-party notices

NA-SSO is licensed under the [MIT License](LICENSE). It depends on the following
third-party packages, which are fetched at install time and are **not** bundled
in this repository. Each remains under its own license, listed below as declared
by the upstream project. Consult each project for the authoritative text.

## Runtime dependencies

| Package | License |
| --- | --- |
| aiosmtplib | MIT |
| fastapi | MIT |
| uvicorn | BSD-3-Clause |
| jinja2 | BSD-3-Clause |
| python-multipart | Apache-2.0 |
| sqlalchemy | MIT |
| httpx | BSD-3-Clause |
| bcrypt | Apache-2.0 |
| cryptography | Apache-2.0 OR BSD-3-Clause |
| itsdangerous | BSD-3-Clause |
| pydantic-settings | MIT |
| pyyaml | MIT |
| asyncssh | EPL-2.0 OR GPL-2.0-or-later |
| webauthn (py_webauthn) | BSD-3-Clause |

`asyncssh` is used as an unmodified dependency; its EPL-2.0 / GPL-2.0 terms are
weak, file-level copyleft that applies to modifications of the package's own
files and does not affect NA-SSO's MIT licensing.

## Development-only dependencies

Not required to run NA-SSO; used for tests and linting: `aiosmtpd`, `playwright`,
`pytest`, `pytest-asyncio`, `pytest-playwright`, `respx`, `httpx2`, `ruff`.
