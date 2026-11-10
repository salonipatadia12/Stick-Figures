# `python -m unittest discover -s tests` puts tests/ first on sys.path and imports this directory as the package
# "webapp", which shadows the real webapp/ package at the repository root. Extend the package path so that
# webapp.app, webapp.core, webapp.testing ... still resolve to the real modules.
import os

__path__.append(os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), 'webapp'))
