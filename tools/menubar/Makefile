# sisoul menu-bar tray Makefile
PYTHON ?= /Users/as/sisoul-dev/.venv/bin/python

.PHONY: help install dev run check self-check clean app open

help:
	@echo "make install     — pip install rumps + py2app + httpx"
	@echo "make dev         — run tray.py in foreground (Ctrl-C to quit)"
	@echo "make self-check  — daemon health + friends list JSON dump (no GUI)"
	@echo "make app         — build dist/Sisoul.app via py2app"
	@echo "make open        — open dist/Sisoul.app (after make app)"
	@echo "make clean       — rm build/ dist/"

install:
	$(PYTHON) -m pip install rumps py2app httpx

dev:
	$(PYTHON) sisoul_tray.py

run: dev

self-check:
	$(PYTHON) sisoul_tray.py --self-check

app:
	bash build_app.sh

open: app
	open dist/Sisoul.app

clean:
	rm -rf build dist *.egg-info __pycache__
