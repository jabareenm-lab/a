#!/bin/bash
cd "$(dirname "$0")"
if [ ! -d .venv ]; then
  python3 -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt
else
  source .venv/bin/activate
fi
[ -f config.yaml ] || cp config.example.yaml config.yaml
if [ ! -f .env ]; then cp .env.example .env; echo "Edit .env and config.yaml, then run again."; open -e .env config.yaml; exit 0; fi
python run.py
