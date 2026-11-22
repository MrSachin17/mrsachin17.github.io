import json, os, re, io, datetime, requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin
from pypdf import PdfReader

H = {"User-Agent": "Mozilla/5.0"}
KW = re.compile(r"recruit|notification|advert|vacanc|career|gate|engineer|trainee", re.I)
MAX_NEW = 25  # cost/time cap per run
KEY = os.environ.get("ANTHROPIC_API_KEY")
GKEY = os.environ.get("GEMINI_API_KEY")  # free tier key from aistudio.google.com
import time
load = lambda f: json.load(open(f)) if os.path.exists(f) else []
seen, jobs = set(load("seen.json")), load("jobs.json")

PROMPT = """You read a job/recruitment page. If it is NOT a recruitment notice or an announcement
of upcoming recruitment (e.g. via GATE 2027), return [].
Otherwise return a JSON array (no other text) of objects with keys:
org, post, type ("PSU"|"Govt"|"Private"), status ("open" or "tentative"),
last_date (YYYY-MM-DD or null), age_max (int, General category, or null),
age_cutoff (YYYY-MM-DD or null), qual (subset of ["Diploma","B.Tech","M.Tech","PhD"], minimum accepted),
branch (list of lowercase branch words, [] if any), domicile (state or null), min_exp (years, 0 if none),
vacancies (int or null), note (short, e.g. "GATE 2027 based, dates awaited"),
syllabus (concise topic list; if not in text, give the standard syllabus for this exam/post),
prep (2-3 lines on how to prepare). Use null for anything not stated; never guess dates."""

def text_of(url):
    r = requests.get(url, headers=H, timeout=30)
    if url.lower().endswith(".pdf") or "pdf" in r.headers.get("content-type", ""):
        pg = PdfReader(io.BytesIO(r.content)).pages[:6]
        return "\n".join(p.extract_text() or "" for p in pg)[:12000]
    return BeautifulSoup(r.text, "html.parser").get_text(" ", strip=True)[:9000]

def regex_extract(txt, title):
    if not re.search(r"recruit|vacanc", txt, re.I): return []
    m = re.search(r"last date[^0-9]{0,40}(\d{1,2})[./-](\d{1,2})[./-](\d{4})", txt, re.I)
    d = f"{m.group(3)}-{int(m.group(2)):02d}-{int(m.group(1)):02d}" if m else None
    return [{"org": "", "post": title, "type": "Govt", "status": "open", "last_date": d,
             "note": "auto-detected, verify details", "syllabus": "", "prep": ""}]

def extract(txt, title=""):
    if GKEY:
        time.sleep(7)  # stay under free-tier rate limit
        r = requests.post("https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent",
            params={"key": GKEY}, timeout=90, json={"contents": [{"parts": [{"text": PROMPT + "\n\n" + txt}]}]})
        out = r.json()["candidates"][0]["content"]["parts"][0]["text"]
        return json.loads(re.search(r"\[.*\]", out, re.S).group(0))
    if not KEY: return regex_extract(txt, title)
    r = requests.post("https://api.anthropic.com/v1/messages", timeout=90,
        headers={"x-api-key": KEY, "anthropic-version": "2023-06-01", "content-type": "application/json"},
        json={"model": "claude-haiku-5-5", "max_tokens": 1500,
              "messages": [{"role": "user", "content": PROMPT + "\n\n" + txt}]})
    out = r.json()["content"][0]["text"]
    return json.loads(re.search(r"\[.*\]", out, re.S).group(0))

links = {}
for page in json.load(open("sources.json"))["pages"]:
    try:
        soup = BeautifulSoup(requests.get(page, headers=H, timeout=30).text, "html.parser")
        for a in soup.find_all("a", href=True):
            u = urljoin(page, a["href"])
            if u.startswith("http") and u not in seen and KW.search(a.get_text() + u):
                links[u] = a.get_text(" ", strip=True)[:100]
    except Exception as e:
        print("page fail", page, e)

for u in list(links)[:MAX_NEW]:
    seen.add(u)
    try:
        for j in extract(text_of(u), links[u]):
            j["link"] = u
            jobs.append(j)
    except Exception as e:
        print("skip", u, e)

today = datetime.date.today().isoformat()
jobs = [j for j in jobs if (j.get("last_date") or "9999") >= today]
uniq = {(j.get("org"), j.get("post"), j.get("last_date")): j for j in jobs}
json.dump(sorted(uniq.values(), key=lambda j: j.get("last_date") or "9999"), open("jobs.json", "w"), indent=1)
json.dump(sorted(seen)[-3000:], open("seen.json", "w"))
