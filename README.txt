Internship finder

Runs every day on GitHub Actions at 6:00 AM India time. It checks several
sources for new internships, scores them against the resume with Groq,
and emails the best ones in one grouped list.

Sources, strongest first
1. Company career pages through Greenhouse, Lever and Ashby.
   The company list is in companies.json, each one checked by hand to have
   a working public feed and a genuine India, remote India, or UAE presence.
2. JSearch on RapidAPI (LinkedIn, Indeed, Glassdoor through Google for Jobs).
   Limited to at most 3 searches a day, usage is tracked in
   jsearch_usage.json so the free monthly plan is never exceeded.
3. Adzuna, searched across Hyderabad, Bangalore, Chennai and all India.
4. Free remote boards: Remotive, RemoteOK, Himalayas.
5. Internshala (minor source now, used to be the only one).

Every source only passes along postings whose title contains intern,
internship, trainee or apprentice, and whose location is South India,
remote with India allowed, or UAE. Full time roles never reach scoring.
Duplicates across sources (same company and title) are removed before
scoring. seen.json remembers what has already been emailed so the same
listing is never sent twice.

Setup
1. Make a PRIVATE GitHub repository and upload everything in this folder,
   including the .github folder.
2. In the repository go to Settings, Secrets and variables, Actions, and add:
   GROQ_API_KEY       your key from console.groq.com
   GMAIL_ADDRESS      the Gmail address that sends and receives the list
   GMAIL_APP_PASSWORD a Google app password (needs 2 step verification on)
   RAPIDAPI_KEY       your key from rapidapi.com for the JSearch API
   ADZUNA_APP_ID      your app id from developer.adzuna.com
   ADZUNA_APP_KEY     your app key from developer.adzuna.com
   RAPIDAPI_KEY, ADZUNA_APP_ID and ADZUNA_APP_KEY are optional, if they are
   missing those two sources are skipped and everything else still runs.
3. Go to the Actions tab, pick "Internship finder", click Run workflow.

Changing things
Cities, stipend minimum and scoring rules are at the top of
internship_finder.py and in sources/common.py.
companies.json is the list of company career pages, add or remove companies
there any time, each needs name, platform (greenhouse, lever or ashby) and
slug.
seen.json is the list of jobs already sent, and jsearch_usage.json is the
JSearch call counter, both updated automatically by the workflow.
