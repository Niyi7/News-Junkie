import asyncio, hashlib, html, os, re, sqlite3, time
from contextlib import asynccontextmanager
from email.utils import parsedate_to_datetime
from pathlib import Path

import feedparser
import httpx
from fastapi import FastAPI, Query
from fastapi.responses import FileResponse, JSONResponse

from feeds import FEEDS

DATA_DIR = Path(os.environ.get("DATA_DIR", Path(__file__).parent))
DATA_DIR.mkdir(parents=True, exist_ok=True)
DB = DATA_DIR / "news.db"
KEEP_DAYS = 14
REFRESH_SECONDS = 15 * 60


def db():
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    return con


def init_db():
    con = db()
    con.executescript("""
    CREATE TABLE IF NOT EXISTS articles (
        id TEXT PRIMARY KEY,
        source TEXT, category TEXT, title TEXT, summary TEXT,
        link TEXT, image TEXT, published INTEGER, fetched INTEGER
    );
    CREATE INDEX IF NOT EXISTS idx_pub ON articles(published DESC);
    CREATE INDEX IF NOT EXISTS idx_cat ON articles(category, published DESC);
    CREATE VIRTUAL TABLE IF NOT EXISTS articles_fts USING fts5(
        title, summary, content='articles', content_rowid='rowid');
    """)
    con.commit(); con.close()


def clean(text: str, limit: int = 400) -> str:
    text = re.sub(r"<[^>]+>", " ", text or "")
    text = re.sub(r"\s+", " ", html.unescape(text)).strip()
    return text if len(text) <= limit else text[:limit].rsplit(" ", 1)[0] + "…"


def entry_time(e) -> int:
    for key in ("published_parsed", "updated_parsed"):
        t = getattr(e, key, None) or e.get(key)
        if t:
            return int(time.mktime(t))
    return int(time.time())


def entry_image(e):
    for m in (e.get("media_thumbnail") or []) + (e.get("media_content") or []):
        if m.get("url"):
            return m["url"]
    for l in e.get("links", []):
        if l.get("type", "").startswith("image"):
            return l.get("href")
    return None


FEED_STATUS = {}
HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; DriftNewsBot/0.2)",
    "Accept": "application/rss+xml, application/atom+xml, application/xml;q=0.9, text/xml;q=0.8, */*;q=0.5",
}


async def fetch_feed(client, feed):
    info = {"ok": False, "status": None, "entries": 0, "added": 0, "error": None, "at": int(time.time())}
    FEED_STATUS[feed["name"]] = info
    try:
        r = await client.get(feed["url"], timeout=20, follow_redirects=True, headers=HEADERS)
        info["status"] = r.status_code
        r.raise_for_status()
        parsed = feedparser.parse(r.content)
        info["entries"] = len(parsed.entries)
        if not parsed.entries and parsed.bozo:
            info["error"] = f"parse: {parsed.bozo_exception}"[:200]
    except Exception as ex:
        info["error"] = f"{type(ex).__name__}: {ex}"[:200]
        print(f"[feed error] {feed['name']}: {info['error']}")
        return 0
    con, added = db(), 0
    for e in parsed.entries:
        link = e.get("link")
        if not link:
            continue
        aid = hashlib.sha1(link.encode()).hexdigest()
        cur = con.execute(
            "INSERT OR IGNORE INTO articles VALUES (?,?,?,?,?,?,?,?,?)",
            (aid, feed["name"], feed["category"], clean(e.get("title", ""), 200),
             clean(e.get("summary", "")), link, entry_image(e),
             min(entry_time(e), int(time.time())), int(time.time())))
        if cur.rowcount:
            added += 1
            con.execute("INSERT INTO articles_fts(rowid, title, summary) "
                        "SELECT rowid, title, summary FROM articles WHERE id=?", (aid,))
    con.commit(); con.close()
    info["ok"] = info["entries"] > 0
    info["added"] = added
    print(f"[feed] {feed['name']}: HTTP {info['status']}, {info['entries']} entries, {added} new")
    return added


def prune():
    con = db()
    cur = con.execute("DELETE FROM articles WHERE published < ?",
                      (int(time.time()) - KEEP_DAYS * 86400,))
    if cur.rowcount:
        con.execute("INSERT INTO articles_fts(articles_fts) VALUES('rebuild')")
    con.commit(); con.close()


async def refresh_all():
    async with httpx.AsyncClient() as client:
        results = await asyncio.gather(*(fetch_feed(client, f) for f in FEEDS))
    prune()
    print(f"[refresh] {sum(results)} new articles")
    return sum(results)


async def refresh_loop():
    while True:
        try:
            await refresh_all()
        except Exception as ex:
            print(f"[refresh error] {ex}")
        await asyncio.sleep(REFRESH_SECONDS)


@asynccontextmanager
async def lifespan(app):
    init_db()
    task = asyncio.create_task(refresh_loop())
    yield
    task.cancel()


app = FastAPI(title="News Aggregator", lifespan=lifespan)


@app.get("/")
def index():
    return FileResponse(Path(__file__).parent / "static" / "index.html")


@app.get("/api/articles")
def articles(category: str | None = None, source: str | None = None,
             q: str | None = None, limit: int = Query(30, le=100), offset: int = 0):
    sql, args = "SELECT a.* FROM articles a", []
    where = []
    if q:
        terms = " ".join(f'"{t}"*' for t in re.findall(r"\w+", q))
        if not terms:
            return []
        sql += " JOIN articles_fts f ON f.rowid = a.rowid"
        where.append("articles_fts MATCH ?"); args.append(terms)
    if category:
        where.append("a.category = ?"); args.append(category)
    if source:
        where.append("a.source = ?"); args.append(source)
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY a.published DESC LIMIT ? OFFSET ?"
    args += [limit, offset]
    con = db()
    rows = [dict(r) for r in con.execute(sql, args)]
    con.close()
    return rows


@app.get("/healthz")
def healthz():
    con = db()
    n = con.execute("SELECT COUNT(*) FROM articles").fetchone()[0]
    con.close()
    return {"ok": True, "articles": n}


@app.get("/api/status")
def status():
    con = db()
    n = con.execute("SELECT COUNT(*) FROM articles").fetchone()[0]
    con.close()
    return {"articles": n, "feeds": FEED_STATUS}


@app.get("/api/meta")
def meta():
    return {"categories": sorted({f["category"] for f in FEEDS}),
            "sources": [f["name"] for f in FEEDS]}


@app.post("/api/refresh")
async def manual_refresh():
    return {"added": await refresh_all()}
