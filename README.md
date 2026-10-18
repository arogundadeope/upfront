# carecost

Ask in plain words what procedure you need and who insures you. Get a recommendation of where to go nearby and what you will pay, built from the price files hospitals and insurers are required to publish, with Pine Computer's agent doing the parts that live behind websites with no API.

## How it works

1. **Hospital registry.** CMS's hospital list for the nine Bay Area counties: 56 acute hospitals with addresses, geocodes and star ratings (`data/registry/hospitals.csv`).
2. **Price files.** Each hospital's `cms-hpt.txt` root file points to its machine-readable standard charges file. `src/fetch.py` downloads them; `src/pine.py` has Pine's agent find them when the root file is missing or dead.
3. **One price table.** `src/parse.py` reads the CMS v2/v3 formats (CSV tall, CSV wide, JSON) into one row shape; `src/load.py` puts them in DuckDB; `src/normalize.py` maps 800 payer labels to insurer, product and line of business; `src/link.py` ties each file to its CMS hospital.
4. **Interpreter.** Pine's agent reads the request, decides the billed components (facility, physician, anesthesia, add-ons), picks the codes, checks them against the index through `/api/codes`, and asks only the questions that change the price (`src/pine.py`, `INTERPRET_GOAL`).
5. **Pricing.** `src/price.py` prices each component at every hospital in range: facility from the hospital's file for that insurer and plan type, physician and anesthesia from the Medicare fee schedule at the local rate times a commercial multiple (labeled as estimates), then deductible and coinsurance, verification (a payer row with zero paid cases or a formula price is "not confirmed"), and ranking by cost with a rating floor.
6. **Page.** `web/index.html` + `web/app.js`: the conversation, the recommendation tile, and the panels (what the total covers, your share, places near you with list and map, the same procedure under other plans). "Find its prices" on a hospital with no file starts a Pine job that fetches, parses, loads and re-prices.

## What Pine does here

- **Interprets the request** (`INTERPRET_GOAL` in `src/pine.py`): the agent decides the billed episode, checks codes against the index through the public API, asks only price-changing questions, writes `episode.json`.
- **Finds hospital files** (`FETCH_GOAL`): root file first, browsing second, system files narrowed to one hospital, download and manifest. Works where plain downloads fail (certificate errors, bot blocks, acquired hospitals whose domains redirect).
- **Shows its work**: progress and activity events plus screen frames stream into the page's working card; a steer box sends instructions to a running turn.
- **Learns**: the Computer is persistent (credentials in `data/pine/`). After the first successful fetch it drafts a skill with a generalizing scaffold; `/api/pine/refine` rewrites a skill; later fetches name the active skill.
- **Parallel**: up to three sessions at once on the Computer, so several hospitals can be fetched together.

## Run

```sh
.venv/bin/uvicorn src.api:app --host 127.0.0.1 --port 8010
cloudflared tunnel --url http://127.0.0.1:8010     # Pine's agent calls /api/codes through this URL
```

`.env` holds `PINE_API_KEY`, `PINE_SANDBOX_ENDPOINT` and `CARECOST_PUBLIC_URL` (the tunnel URL). Rebuild the index with `scripts/fetch_bay_area.py`, then `src/load.py`, `src/normalize.py`, `src/link.py`, `scripts/publish_db.sh`.

## Honest limits

- Hospital files cover facility fees only. Physician and anesthesia lines are estimates from Medicare rates, and the page labels them.
- Kaiser hospitals are skipped (Kaiser plans only). Three Bay Area hospitals block automated downloads; two (Saint Francis, St. Mary's) publish no file we could find.
- Deductible and coinsurance come from the person; nothing public knows them.
