"""The Mac worker: pulls paint/render jobs (and pitch/script with SCRIPT_PROVIDER=ollama) from the VPS over HTTPS and
runs them in this checkout (docs/WEBAPP.md).

  .venv/bin/python -m macworker            # the loop (launchd runs this)
  .venv/bin/python -m macworker --once     # claim at most one job, then exit
  .venv/bin/python -m macworker --dry-run  # check config, token and the server; claim nothing
"""
VERSION = '0.1.0'
