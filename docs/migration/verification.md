# Local candidate verification

Source: `bcb73fd3804ff253fe87017da3b843dc606c858d` (private legacy main).
Candidate: `D:/Project/shioaji-stock-prices-new`, branch `rebuild/producer-candidate`.
The new repository has no remote, inherited Git objects, alternates or tags.

- `uv sync --offline --frozen` succeeded using the existing cache and lockfile.
- Full offline pytest after fixture conversion: **821 passed, 2 skipped**.
- Operative document links resolve; the full 265-row source mapping carries
  original SHA-256 and candidate hashes. The final verification receipt is local
  under `.tmp/rebuild/`; original source files remain in the private legacy commit.
- The first normal commit hook run passed secret detection, UTF-8 hygiene,
  Pyright and Ruff lint. Prettier/Ruff formatted candidate files; Mypy exposed
  25 baseline errors in five files when checking the complete new root.
- Follow-up candidate fixes annotate existing empty collections and the revenue
  response dictionary, and replace test mocks that relied on `list.append()` as
  an expression with an explicit recorder. Acquisition logic is unchanged.
  Offline filename-discovery regression tests cover the dividends entry point.
  The affected-file suite passed: **103 passed, 1 skipped**. The successful normal
  root commit passed every installed hook, including Mypy and secret detection.
- The Mypy regression test explicitly exposes installed-module diagnostics;
  otherwise editable installation hides errors in repo-local `.tmp` fixtures.

No live acquisition, production-data validation, scheduler registration, backup,
broker login, public repository creation or push was performed. The source SDK
emits a Pydantic deprecation warning. Skipped tests and the warning are not a
production certification. Data/category and third-party source-material licensing
review remains required before publication; see [publication gate](README.md).
