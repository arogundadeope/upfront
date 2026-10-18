#!/bin/sh
# Copy the working database to the serving copy the API reads. Run after a load finishes.
cd /Users/ope/carecost
cp data/db/carecost.duckdb data/db/serving.duckdb.tmp && mv data/db/serving.duckdb.tmp data/db/serving.duckdb && echo "published $(date)"
