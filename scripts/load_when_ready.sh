#!/bin/sh
cd /Users/ope/carecost
while ! grep -q "ALL DONE" data/raw/fetch_bay_area.out 2>/dev/null; do sleep 15; done
{
  .venv/bin/python src/load.py
  .venv/bin/python src/normalize.py
  .venv/bin/python src/link.py
  echo "LOAD DONE"
} > data/db/load_all.out 2>&1
