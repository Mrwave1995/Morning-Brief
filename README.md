# Morning Brief

A personal daily news brief that writes itself every morning and lives on your phone like an app.

Every day at about 6:00 Dubai time, a GitHub Action:

1. pulls fresh headlines from free Google News feeds,
2. asks Claude (with web search) to research and write the brief in your sections,
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
- Claude: web search is $10 per 1,000 searches. At up to 12 searches a day that's at most about $3.60/month, plus a little for tokens with Claude Haiku. Expect roughly $4–6/month in total.
- To spend less, lower `MAX_SEARCHES` in the workflow `env`.

## Changing what it covers
Open `brief.py`:
- `READER_PROFILE` describes you (kept general because the repo is public).
- `SECTIONS` lists each section, what it should cover, how many stories, and which Google News searches seed it. Add, remove or reword freely.

## Handy
- Run it any time: **Actions → Morning Brief → Run workflow**.
- Pause it: **Actions → Morning Brief → ⋯ → Disable workflow**.
- Old briefs are kept for 45 days; use the ‹ › arrows in the app to go back.
- Test locally without a key: `python brief.py --mock`.
