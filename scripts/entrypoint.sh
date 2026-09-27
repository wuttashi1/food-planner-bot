#!/bin/sh
set -eu
python -m scripts.check_env
alembic upgrade head
exec python main.py
