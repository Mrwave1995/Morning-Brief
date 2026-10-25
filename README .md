# Morning Brief

A personal daily news brief that writes itself every morning and lives on your phone like an app.

Every day at about 6:00 Dubai time, a GitHub Action:

1. pulls fresh headlines from free Google News feeds, plus 5 SerpApi news searches with article snippets for your niche topics,
2. asks Claude Haiku to pick what matters and write the brief in your sections, linking each story to the articles it came from,
3. saves it to `docs/data/`, which GitHub Pages serves to the app,
4. optionally sends a "brief is ready" alert to your phone through ntfy.

Sections: Spotlight · Indian politics · Andaman & Nicobar / Environment · Insurance & health · UAE & Gulf · Money & markets · Hidden gems.

---

## Setup (about 10 minutes, once)

### 1. Create the repo
On GitHub: **+ → New repository** → name it `morning-brief` → **Public** → Create.
(Public is needed for free GitHub Pages. Only the news is visible; your API key stays secret.)

### 2. Upload the files
On the new repo page: **Add file → Upload files**, then drag in **everything** from the unzipped folder, including the `.github` folder. Commit.

If the `.github` folder doesn't come across (Windows sometimes hides it):
**Add file → Create new file**, type `.github/workflows/brief.yml` as the name, paste in the contents of that file, commit.

### 3. Add your Claude API key
**Settings → Secrets and variables → Actions → New repository secret**
- Name: `ANTHROPIC_API_KEY`
- Value: your key from console.anthropic.com (secrets can't be copied between repos, so paste it again here)

Optional but recommended: add a second secret `SERPAPI_KEY` with your SerpApi key. It adds 5 targeted news searches a day (with article snippets) for politics, Great Nicobar, insurance and markets. Without it, the brief still works from the free feeds.

### 4. Let the Action save the brief
**Settings → Actions → General → Workflow permissions** → choose **Read and write permissions** → Save.

### 5. Turn on the website
**Settings → Pages** → Source: **Deploy from a branch** → Branch: `main`, folder: `/docs` → Save.
After a minute it's live at `https://<your-username>.github.io/morning-brief/`.

### 6. Make the first brief now
**Actions → Morning Brief → Run workflow**. It takes 2–4 minutes. After that it runs by itself every morning.

### 7. Install it on your phone
Open the link from step 5 on your phone.
- **Android (Chrome):** tap **Install** on the banner, or ⋮ menu → **Add to Home screen**.
- **iPhone (Safari):** Share button → **Add to Home Screen**.

### 8. (Optional) Morning alert
1. Install the free **ntfy** app (Play Store / App Store).
2. In the app, subscribe to a topic with a long random name nobody would guess, e.g. `brief-7f3k9q2xw`.
3. Add a repo secret `NTFY_TOPIC` with that exact name.

You'll get a notification with the top headlines each morning; tapping it opens the app.

---

## Cost
- GitHub Actions and Pages: free.
- Google News feeds: free.
- SerpApi: 5 searches a day, about 150 a month from your plan. Change `SERPAPI_PER_DAY` in the workflow to use more or fewer. If credits run out, it quietly falls back to the free feeds.
- Claude Haiku: about half a US cent per brief, so roughly **$0.15–0.30 a month**.
- Optional: for fresher market numbers you can let Claude run a few web searches ($10 per 1,000). Add `MAX_SEARCHES: 3` under `env:` in `.github/workflows/brief.yml`; that adds about $0.90 a month.

## Changing what it covers
Open `brief.py`:
- `READER_PROFILE` describes you (kept general because the repo is public).
- `SECTIONS` lists each section, what it should cover, how many stories, and which Google News searches seed it. Add, remove or reword freely.

## Handy
- Run it any time: **Actions → Morning Brief → Run workflow**.
- Pause it: **Actions → Morning Brief → ⋯ → Disable workflow**.
- Old briefs are kept for 45 days; use the ‹ › arrows in the app to go back.
- Test locally without a key: `python brief.py --mock`.
