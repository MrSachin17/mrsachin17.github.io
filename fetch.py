import json, os, re, io, datetime, requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin, urlparse
from pypdf import PdfReader

H = {"User-Agent": "Mozilla/5.0"}
KW = re.compile(r"recruit|notification|advert|vacanc|career|gate|engineer|trainee", re.I)
MAX_NEW = 30  # cost/time cap per run
KEY = os.environ.get("ANTHROPIC_API_KEY")
GKEY = os.environ.get("GEMINI_API_KEY")  # free tier key from aistudio.google.com
import time, smtplib
from email.message import EmailMessage
load = lambda f: json.load(open(f)) if os.path.exists(f) else []
seen, jobs = set(load("seen.json")), load("jobs.json")
key = lambda j: f"{j.get('org')}|{j.get('post')}|{j.get('last_date')}"
old_keys = {key(j) for j in jobs}
try: prof = json.loads(os.environ.get("PROFILE_JSON") or "null")
except Exception: prof = None
QR = {"Diploma": 0, "B.Tech": 1, "M.Tech": 2, "PhD": 3}
RELAX = {"General": 0, "EWS": 0, "OBC": 3, "SC": 5, "ST": 5}

def eligible(j):
    """True / False / None (cannot tell)."""
    if not prof: return None
    try:
        if j.get("age_max"):
            dob = datetime.date.fromisoformat(prof["dob"])
            at = datetime.date.fromisoformat(j["age_cutoff"]) if j.get("age_cutoff") else datetime.date.today()
            a = at.year - dob.year - ((at.month, at.day) < (dob.month, dob.day))
            if a > j["age_max"] + RELAX.get(prof.get("category"), 0): return False
        q = j.get("qual") or []
        if q:
            need = min(QR.get(x, 9) for x in q); held = prof.get("quals", [])
            if not any(h in q for h in held) and max([-1] + [QR.get(h, -1) for h in held]) < need: return False
        b = [x.lower() for x in (j.get("branch") or [])]; mine = prof.get("branch", "").lower()
        if b and mine and not any(x in mine or mine in x for x in b): return False
        if j.get("domicile"):
            if not prof.get("domicile"): return None
            if j["domicile"].lower() != prof["domicile"].lower(): return False
        if (j.get("min_exp") or 0) > prof.get("exp", 0): return False
        return True
    except Exception:
        return None

PROMPT = """You read a job/recruitment page. If it is NOT a recruitment notice or an announcement
of upcoming recruitment (e.g. via GATE 2027), return [].
Otherwise return a JSON array (no other text) of objects with keys:
org, post, type ("PSU"|"Govt"|"Private"), status ("open" or "tentative"),
last_date (YYYY-MM-DD or null), age_max (int, General category, or null),
age_cutoff (YYYY-MM-DD or null), qual (subset of ["Diploma","B.Tech","M.Tech","PhD"], minimum accepted),
branch (list of lowercase branch words, [] if any), domicile (state or null), min_exp (years, 0 if none),
vacancies (int or null), note (short, e.g. "GATE 2027 based, dates awaited"),
syllabus (concise topic list; if not in text, give the standard syllabus for this exam/post),
prep (2-3 lines on how to prepare), apply_url (official application page URL if stated in the text), pay (salary or pay scale as stated, else null), selection (selection process in a few words, e.g. "GATE 2027 score + interview"). Use null for anything not stated; never guess dates."""

AGG = ("freejobalert", "sarkariresult", "indgovtjobs", "sarkarijobfind", "rojgarresult", "employmentnews", "ncs.gov")

def read(r, u):
    """Return (text, official_link). official_link = first off-site PDF/notification link in an article."""
    if u.lower().endswith(".pdf") or "pdf" in r.headers.get("content-type", ""):
        pg = PdfReader(io.BytesIO(r.content)).pages[:6]
        return "\n".join(p.extract_text() or "" for p in pg)[:12000], None
    soup = BeautifulSoup(r.text, "html.parser")
    host, pdfs, others = urlparse(u).netloc, [], []
    for a in soup.find_all("a", href=True):
        h = urljoin(u, a["href"]); d = urlparse(h).netloc
        if not d or d == host or any(x in d for x in AGG): continue
        if h.lower().endswith(".pdf"): pdfs.append(h)
        elif re.search(r"notification|advertis|official|apply", a.get_text(), re.I): others.append(h)
    off = (pdfs + others + [None])[0]
    return soup.get_text(" ", strip=True)[:9000], off

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
        last = ""
        for model in ("gemini-2.5-flash", "gemini-flash-latest", "gemini-2.0-flash"):
            time.sleep(7)  # stay under free-tier rate limit
            r = requests.post(f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
                params={"key": GKEY}, timeout=90, json={"contents": [{"parts": [{"text": PROMPT + "\n\n" + txt}]}]})
            d = r.json()
            if "candidates" in d:
                out = d["candidates"][0]["content"]["parts"][0]["text"]
                return json.loads(re.search(r"\[.*\]", out, re.S).group(0))
            last = f"{model} -> HTTP {r.status_code}: {r.text[:300]}"
            print("GEMINI ERROR", last)
        raise RuntimeError(last)
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
            if u.startswith("http") and "#" not in u and u not in seen and KW.search(a.get_text() + u):
                links[u] = (a.get_text(" ", strip=True)[:100], page)
    except Exception as e:
        print("page fail", page, e)

for u in list(links)[:MAX_NEW]:
    seen.add(u)
    try:
        title, page = links[u]
        txt, off = read(requests.get(u, headers=H, timeout=30), u)
        res = extract(txt, title)
        for j in res:
            j["link"] = u; j["source"] = page; j["verified"] = False
        if res and off:  # cross-check against the official notification
            try:
                txt2, _ = read(requests.get(off, headers=H, timeout=30), off)
                res2 = extract(txt2, title)
                if res2:
                    for j in res2:
                        j["link"] = off; j["info_link"] = u; j["source"] = page; j["verified"] = True
                    res = res2
            except Exception as e:
                print("official skip", off, e)
        jobs.extend(res)
    except Exception as e:
        print("skip", u, e)

today = datetime.date.today().isoformat()
uniq = {(j.get("org"), j.get("post"), j.get("last_date")): j for j in jobs}
json.dump(sorted(uniq.values(), key=lambda j: j.get("last_date") or "9999"), open("jobs.json", "w"), indent=1)
json.dump(sorted(seen)[-3000:], open("seen.json", "w"))

# ---- email alerts: new eligible notices + eligible ones closing within 3 days ----
alerted = set(load("alerted.json"))
td = datetime.date.today(); alerts = []
for j in uniq.values():
    if eligible(j) is False: continue
    if j.get("last_date") and j["last_date"] < td.isoformat(): continue
    k = key(j)
    if k not in old_keys and k + "|new" not in alerted:
        alerts.append(("NEW", j)); alerted.add(k + "|new")
    elif j.get("last_date") and 0 <= (datetime.date.fromisoformat(j["last_date"]) - td).days <= 3 and k + "|soon" not in alerted:
        alerts.append(("CLOSING SOON", j)); alerted.add(k + "|soon")
json.dump(sorted(alerted)[-3000:], open("alerted.json", "w"))
U, P, T = os.environ.get("MAIL_USER"), os.environ.get("MAIL_APP_PASSWORD"), os.environ.get("MAIL_TO")
if alerts and U and P and T:
    m = EmailMessage(); m["Subject"] = f"Job Tracker: {len(alerts)} update(s)"; m["From"] = U; m["To"] = T
    m.set_content("\n\n".join(f"[{t}] {j.get('post')} - {j.get('org')} ({j.get('type')})\nLast date: {j.get('last_date') or 'tentative'}\n{j.get('note') or ''}\n{j.get('link')}" for t, j in alerts))
    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as s: s.login(U, P); s.send_message(m)
