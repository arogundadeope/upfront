# Demo run sheet

## Before you walk up
- Terminal 1: API running (`.venv/bin/uvicorn src.api:app --host 127.0.0.1 --port 8010`). Check http://127.0.0.1:8010 loads.
- Terminal 2: tunnel running (`cloudflared tunnel --url http://127.0.0.1:8010`). Its URL must match `CARECOST_PUBLIC_URL` in `.env`; if the tunnel restarted, update `.env` and restart the API.
- Chrome open on http://127.0.0.1:8010, location permission allowed.
- Backup recording open in another tab.
- Start one run 60 seconds before you present so a result is on screen if the live one is slow.

## The story (2.5 minutes)
1. The problem, one sentence: hospitals and insurers publish their prices, but in huge files scattered across hundreds of websites, so nobody can answer "where should I go and what will it cost me".
2. Type a request live. Good ones: "I need an MRI of my knee, I have Cigna through work", "I think I need a hernia repair, Blue Shield PPO", "My doctor wants me to get a colonoscopy, Aetna".
3. While Pine works, say what is happening: Pine's agent is deciding what the procedure involves as it is billed, checking the codes against our index of 44 hospital price files for the Bay Area, and asking only the questions that change the price.
4. Answer the questions and the deductible. Point at the tile: the recommendation, what you pay, total, rating, distance.
5. Scroll: what the total covers, your share, the places near you (flip to the map), the same procedure under other plans.
6. The Pine moment: in the places list, a hospital with no price list has a "Find its prices" button. Click it. Pine's agent goes to that hospital's website, finds the machine-readable file, downloads it, we parse it, and the page re-prices with it included. Its screen shows in the row while it works.
   Say: this Computer is persistent. After its first fetch it wrote itself a skill for finding any hospital's price file, and every fetch since has used it (San Ramon and Santa Clara Valley, both of which block plain downloads, took under a minute each).
   Optional: type into the steer box while the agent works ("take only the main campus file") to show a person can redirect it mid-run.
7. Close: every number traces to a published file; Pine is the hands and the brain for anything that lives behind a website with no API.

## If something stalls
- Agent slow: keep talking, or switch to the backup recording. Never debug on stage.
- "Find its prices" fails: say the agent reported the hospital does not publish one, which is itself an answer.

## Numbers you can quote
- 56 acute hospitals in the nine Bay Area counties, 38 with a price file in the index.
- 44 files, 13.1 million price rows, 931 payer labels normalized.
- Pine Computer in 4 seconds; an agent turn in 20 to 40 seconds; a hospital fetched, parsed and indexed in about 40 seconds.
- Pine features in use: agent runs with questions, event stream with screen frames, steering, persistent Computer, learned and refined skills, three parallel sessions.
