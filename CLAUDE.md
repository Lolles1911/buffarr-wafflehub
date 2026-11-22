# Instructions for Claude

## Git workflow

- The owner has given standing permission to commit and push directly to `main`.
- Do not create feature branches or pull requests unless the owner asks for one in that session.
- Before pushing, run the test suite (`python -m pytest -q` with `requirements-dev.txt` installed) and push only when it passes.
- Remember that every push to `main` triggers `.github/workflows/docker-publish.yml`, which publishes a new Docker image.
