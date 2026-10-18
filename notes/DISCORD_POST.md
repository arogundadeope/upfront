**Pod 02 — Upfront**

**What it does**
You describe what happened in your own words ("I have a broken arm, I'm paying cash") and Upfront tells you where to go nearby and what it will cost: one recommendation, the bill broken into parts, every hospital near you compared, and how the price changes with your plan. Every number comes from a price the hospital published. Hospitals are required to publish these by law, but they're huge files in different formats on every hospital's own website, so nobody can actually use them. Index today: 45 files, 13 million prices, 39 of the 56 acute hospitals in the Bay Area.

**How we used Pine Computer**
Pine's agent is both the brain and the hands.
• It reads the request and works out how a hospital would actually bill it (ED visit, X-ray, splint, the doctors), checks the codes against our index through an API, and asks only the questions that change the price.
• When a hospital's price file is somewhere a script can't reach (certificate errors, bot blocks, acquired hospitals whose domains redirect), the agent opens the website in its browser, finds the file, downloads it, and the app re-prices live. Three hospitals that blocked every plain download today were indexed this way in under a minute each.
• The Computer is persistent. After its first successful fetch it learned a skill for finding any hospital's price file, which later fetches use.
• The agent's screen frames and progress stream into the page while it works, and a steer box lets you redirect it mid-run.

**Team**
Ope Arogundade

Video attached (83 seconds).
