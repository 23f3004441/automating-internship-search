Internship finder

Runs every day on GitHub Actions at 6:00 AM India time, reads Internshala,
scores new listings against the resume with Groq, and emails the best ones.

Setup
1. Make a PRIVATE GitHub repository and upload everything in this folder,
   including the .github folder.
2. In the repository go to Settings, Secrets and variables, Actions, and add:
   GROQ_API_KEY       your key from console.groq.com
   GMAIL_ADDRESS      the Gmail address that sends and receives the list
   GMAIL_APP_PASSWORD a Google app password (needs 2 step verification on)
3. Go to the Actions tab, pick "Internship finder", click Run workflow.

Changing things
Cities, stipend minimum and scoring rules are at the top of internship_finder.py.
seen.json is the list of jobs already sent. The workflow updates it by itself.
