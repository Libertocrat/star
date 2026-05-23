"""Action-layer test package for STAR.

This file ensures nested test modules under `tests/actions` have unique module
names during static analysis, avoiding collisions between local `conftest.py`
files in different test directories.
"""
