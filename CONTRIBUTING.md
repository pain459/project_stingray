# Contributing to Project Stingray

## Development setup

1. Clone the repository.
2. Install [uv](https://docs.astral.sh/uv/).
3. Create the environment and install dependencies:
   ```sh
   uv sync --all-extras
   ```
4. Run the test suite (mock mode, no hardware required):
   ```sh
   uv run pytest
   ```

## Code quality

- Lint: `uv run ruff check .`
- Type check: `uv run mypy src/`
- Format: `uv run ruff format .`

## Branching and PRs

- Branch names: `feature/STG-###-short-description` or `fix/STG-###-short-description`.
- All changes go through a pull request to `main`.
- PRs must link the task, describe tests performed, before/after behavior,
  safety impact, and attach evidence.

## Hardware tests

Hardware-dependent tests are marked `@pytest.mark.hardware` and are excluded
from normal CI. Run them explicitly:

```sh
uv run pytest -m hardware
```

## Secrets

Never commit secrets, tokens, or credentials. Use `.env` (git-ignored) for
secrets and `.env.example` for the template.
