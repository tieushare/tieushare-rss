import json
import urllib.request
import xml.etree.ElementTree as ET
import html
import re
import os
from datetime import datetime, timezone, timedelta
from email.utils import format_datetime, parsedate_to_datetime
from html.parser import HTMLParser

# ---------------------------------------------------------------------------
# Cấu hình
# ---------------------------------------------------------------------------
FEED_BASE = "https://www.tieushare.com/feeds/posts/default?alt=rss"
PAGE_SIZE = 69
OPENSEARCH = "{http://a9.com/-/spec/opensearch/1.1/}"

MAIN_SITE_URL = "https://www.tieushare.com"

SITE_NAME = "Tiểu Share"
SITE_TITLE = "Tiểu Share – Bài viết mới nhất về công nghệ & thủ thuật"
SITE_DESCRIPTION = (
    "Tiểu Share chia sẻ kiến thức và thủ thuật về Facebook, TikTok, Instagram, "
    "YouTube, công nghệ, lập trình, Java và phần mềm máy tính."
)

# URL công khai của trang GitHub Pages (không có dấu / ở cuối)
SITE_URL = os.environ.get(
    "GITHUB_PAGES_URL",
    "https://tieushare.github.io/tieushare-rss"
).rstrip("/")
PAGE_URL = SITE_URL + "/"

# True  = cho phép Google/Bing lập chỉ mục trang này
# False = thêm noindex (dùng khi bạn chỉ muốn trang này làm nguồn RSS)
INDEXABLE = True

OUTPUT_DIR = "public"
MAX_ITEMS = 200

VN_TZ = timezone(timedelta(hours=7))

ICON_PREFIX = (
    "https://blogger.googleusercontent.com/img/b/R29vZ2xl/"
    "AVvXsEgj2sqsWqUnoyCUJKVmFn25cp6TLjbmx0gfGVVgQMV6bXfYNfTObR1H81vSqc2DL-nmNWTYugOu6_E9GNZZ9uCKmnkXba_2gBzgSX0W8GH4NJKJkTSMjOw6mnyuIwwP3KGNIlbb29Re3khCyCngzBX9a3umwxFZ8jPXAk6421bD1f6gqrXxbG-I_5w15pk"
)
OG_IMAGE = (
    "https://blogger.googleusercontent.com/img/b/R29vZ2xl/"
    "AVvXsEgoEXABfCseyLjgKJOGz7_oF-tZ-nk1Z4RlGq2ifLZwEANfyi8y9Aw4czHgW0i-FrN6gSBM8CszG6vg2ipkicPg4pLa5A-FueTN5KDRKSlRxW5B76gQXm_gpz24MNR9hWcIzXdxm63oozKB0otHARb-dbFzbuaTmj6Nl5mdC7v9ot5yd8cat8ZV_FI5U9I"
    "/w1280-h720-p-k-no-nu/meta-image.png"
)


def icon_url(size):
    return f"{ICON_PREFIX}/w{size}-h{size}-p-k-no-nu/tieushare.jpeg"


# ---------------------------------------------------------------------------
# Xử lý nội dung
# ---------------------------------------------------------------------------
class TextExtractor(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts = []

    def handle_data(self, data):
        self.parts.append(data)

    def get_text(self):
        return " ".join(self.parts)


def clean_html(value):
    parser = TextExtractor()
    try:
        parser.feed(value or "")
        return re.sub(r"\s+", " ", parser.get_text()).strip()
    except Exception:
        return value or ""


YOUTUBE_THUMB = re.compile(
    r"https?://(?:img\.youtube\.com|i\d?\.ytimg\.com)/vi(?:_webp)?/([\w-]{11})/"
)


def enlarge_image(url):
    """Ask Blogger/Google (or YouTube) for a large version of an image."""
    # YouTube: default.jpg is only 120x90, maxresdefault.jpg is 1280x720
    youtube = YOUTUBE_THUMB.match(url)
    if youtube:
        return f"https://img.youtube.com/vi/{youtube.group(1)}/maxresdefault.jpg"

    url = re.sub(r"/s\d+(?:-[a-z]*\d*)*/", "/s1200/", url)
    url = re.sub(r"/w\d+-h\d+(?:-[a-z]+)*/", "/s1200/", url)
    url = re.sub(r"=s\d+(?:-[a-z]*\d*)*$", "=s1200", url)
    url = re.sub(r"=w\d+-h\d+(?:-[a-z]+)*$", "=s1200", url)
    return url


def resize_image(url, size):
    """Turn a normalized /s1200/ (or =s1200) URL into another size."""
    if "/s1200/" in url:
        return url.replace("/s1200/", f"/s{size}/")
    return re.sub(r"=s1200$", f"=s{size}", url)


def is_resizable(url):
    return "/s1200/" in url or url.endswith("=s1200")


CARD_SIZES = "(max-width: 640px) 100vw, (max-width: 900px) 50vw, 360px"
FEATURED_SIZES = "(max-width: 640px) 100vw, 600px"


def build_image_html(url, alt="", featured=False):
    """<img> with srcset so the browser picks the sharpest size it needs."""
    if not url:
        return '<div class="thumb-fallback" aria-hidden="true"></div>'

    safe = lambda value: html.escape(value, quote=True)
    alt = safe(alt)
    loading = 'loading="eager" fetchpriority="high"' if featured else 'loading="lazy"'

    if "img.youtube.com/vi/" in url and url.endswith("/maxresdefault.jpg"):
        # Not every video has a maxres thumbnail; fall back to hqdefault (480x360)
        fallback = (
            "this.onerror=null;"
            "this.src=this.src.replace('maxresdefault','hqdefault')"
        )
        return (
            f'<img src="{safe(url)}" alt="{alt}" decoding="async" '
            f'{loading} onerror="{fallback}">'
        )

    if not is_resizable(url):
        return f'<img src="{safe(url)}" alt="{alt}" {loading}>'

    sizes = FEATURED_SIZES if featured else CARD_SIZES
    srcset = ", ".join(
        f"{safe(resize_image(url, w))} {w}w" for w in (480, 800, 1200)
    )
    return (
        f'<img src="{safe(resize_image(url, 800))}" srcset="{srcset}" '
        f'sizes="{sizes}" alt="{alt}" decoding="async" {loading}>'
    )


def get_image(item):
    # 1. Image embedded in the post content (usually full size)
    for element in item.iter():
        if element.tag.endswith(("encoded", "description")):
            content = element.text or ""
            match = re.search(
                r'<img[^>]+src=["\']([^"\']+)["\']',
                content,
                re.IGNORECASE
            )
            if match:
                return enlarge_image(match.group(1))

    # 2. RSS enclosure
    for element in item.findall("enclosure"):
        if element.attrib.get("type", "").startswith("image/"):
            return enlarge_image(element.attrib.get("url", ""))

    # 3. Blogger media:thumbnail (tiny, last resort)
    for element in item.iter():
        if element.tag.endswith("thumbnail"):
            url = element.attrib.get("url")
            if url:
                return enlarge_image(url)

    return ""


def get_text(item, tag):
    element = item.find(tag)
    return element.text.strip() if element is not None and element.text else ""


def format_date(value):
    """16/11/2026"""
    try:
        return parsedate_to_datetime(value).astimezone(VN_TZ).strftime("%d/%m/%Y")
    except Exception:
        return value or ""


def iso_date(value):
    """2026-11-16 (dùng cho thuộc tính datetime của thẻ <time>)"""
    try:
        return parsedate_to_datetime(value).astimezone(VN_TZ).date().isoformat()
    except Exception:
        return ""


# ---------------------------------------------------------------------------
# Lấy RSS từ Blogger
# ---------------------------------------------------------------------------
def fetch_xml(url):
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "TieuShareRSSBot/1.0"}
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        return ET.fromstring(response.read())


def fetch_rss():
    items = []
    skipped = 0
    start = 1
    total = None

    while True:
        root = fetch_xml(
            f"{FEED_BASE}&max-results={PAGE_SIZE}&start-index={start}"
        )

        if total is None:
            total = int(root.findtext(f".//{OPENSEARCH}totalResults") or 0)
            print(f"Blogger reports {total} posts in total.")

        raw_items = root.findall(".//item")
        if not raw_items:
            break

        for item in raw_items:
            title = get_text(item, "title")
            link = get_text(item, "link")

            if not title or not link:
                skipped += 1
                continue

            items.append({
                "title": title,
                "link": link,
                "description": clean_html(get_text(item, "description"))[:500],
                "pub_date": get_text(item, "pubDate"),
                "image": get_image(item),
            })

        start += len(raw_items)
        if total and start > total:
            break

    # Remove duplicate URLs (keeps feed order, newest first)
    unique = {}
    for item in items:
        unique[item["link"]] = item

    print(
        f"Read {len(items)} posts, skipped {skipped} without title/link, "
        f"{len(unique)} left after removing duplicates."
    )

    return list(unique.values())[:MAX_ITEMS]


# ---------------------------------------------------------------------------
# Phần <head> (SEO)
# ---------------------------------------------------------------------------
def build_json_ld(items):
    data = {
        "@context": "https://schema.org",
        "@graph": [
            {
                "@type": "WebSite",
                "@id": PAGE_URL + "#website",
                "url": PAGE_URL,
                "name": SITE_NAME,
                "description": SITE_DESCRIPTION,
                "inLanguage": "vi",
            },
            {
                "@type": "CollectionPage",
                "@id": PAGE_URL + "#page",
                "url": PAGE_URL,
                "name": SITE_TITLE,
                "inLanguage": "vi",
                "isPartOf": {"@id": PAGE_URL + "#website"},
                "mainEntity": {
                    "@type": "ItemList",
                    "itemListElement": [
                        {
                            "@type": "ListItem",
                            "position": position,
                            "url": item["link"],
                            "name": item["title"],
                        }
                        for position, item in enumerate(items[:30], start=1)
                    ],
                },
            },
        ],
    }
    # Tránh chuỗi "</script>" trong dữ liệu làm hỏng thẻ script
    return json.dumps(data, ensure_ascii=False).replace("</", "<\\/")


def build_head(items):
    esc = lambda value: html.escape(value, quote=True)
    robots = (
        "index,follow,max-image-preview:large"
        if INDEXABLE else "noindex,follow"
    )

    icons = "\n".join(
        [f'<link rel="apple-touch-icon" sizes="180x180" href="{icon_url(180)}">']
        + [
            f'<link rel="icon" sizes="{s}x{s}" href="{icon_url(s)}">'
            for s in (16, 32, 96, 192)
        ]
    )

    return f"""<title>{esc(SITE_TITLE)}</title>
<meta name="description" content="{esc(SITE_DESCRIPTION)}">
<meta name="robots" content="{robots}">
<meta name="theme-color" content="#0b3a55">
<link rel="canonical" href="{esc(PAGE_URL)}">
<link rel="alternate" type="application/rss+xml" title="{esc(SITE_NAME)} RSS" href="{esc(SITE_URL)}/feed.xml">

<meta name="yandex-verification" content="a6ffcfd1660beece">
<meta name="msvalidate.01" content="D248AE1124FBF7EFD5C29B97010FC431" />
{icons}

<meta property="og:locale" content="vi_VN">
<meta property="og:type" content="website">
<meta property="og:site_name" content="{esc(SITE_NAME)}">
<meta property="og:title" content="{esc(SITE_TITLE)}">
<meta property="og:description" content="{esc(SITE_DESCRIPTION)}">
<meta property="og:url" content="{esc(PAGE_URL)}">
<meta property="og:image" content="{esc(OG_IMAGE)}">
<meta property="og:image:width" content="1280">
<meta property="og:image:height" content="720">
<meta property="og:image:alt" content="{esc(SITE_NAME)}">

<meta name="twitter:card" content="summary_large_image">
<meta name="twitter:title" content="{esc(SITE_TITLE)}">
<meta name="twitter:description" content="{esc(SITE_DESCRIPTION)}">
<meta name="twitter:image" content="{esc(OG_IMAGE)}">
<meta name="twitter:image:alt" content="{esc(SITE_NAME)}">

<script type="application/ld+json">{build_json_ld(items)}</script>"""


# ---------------------------------------------------------------------------
# Tạo trang HTML
# ---------------------------------------------------------------------------
def generate_html(items):
    cards = []

    for index, item in enumerate(items):
        title = html.escape(item["title"])
        link = html.escape(item["link"], quote=True)
        description = html.escape(item["description"])
        pub_date = html.escape(format_date(item["pub_date"]))
        iso = iso_date(item["pub_date"])
        datetime_attr = f' datetime="{iso}"' if iso else ""
        image_html = build_image_html(
            item["image"], alt=item["title"], featured=(index == 0)
        )

        cards.append(f"""
        <article class="post">
            <a class="thumb" href="{link}" target="_blank" rel="noopener"
               tabindex="-1" aria-hidden="true">{image_html}</a>
            <div class="post-content">
                <time class="date"{datetime_attr}>{pub_date}</time>
                <h2><a href="{link}" target="_blank" rel="noopener">{title}</a></h2>
                <p class="summary">{description}</p>
                <a class="read-more" href="{link}" target="_blank" rel="noopener">Đọc bài viết</a>
            </div>
        </article>
        """)

    updated = datetime.now(VN_TZ).strftime("%d/%m/%Y %H:%M (GMT+7)")
    year = datetime.now(VN_TZ).year
    head = build_head(items)

    return f"""<!DOCTYPE html>
<html lang="vi">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
{head}

<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Be+Vietnam+Pro:wght@400;500;600;700;800&display=swap" rel="stylesheet">

<style>
:root {{
    --bg: #f2f5f8;
    --surface: #ffffff;
    --ink: #10242f;
    --muted: #64768a;
    --line: #e2e8ee;
    --brand: #0b3a55;
    --brand-2: #0f6b8f;
    --accent: #f08a24;
    --accent-ink: #b45f0c;
    --shadow: 0 1px 2px rgba(16,36,47,.06), 0 8px 24px rgba(16,36,47,.06);
    --shadow-hover: 0 2px 4px rgba(16,36,47,.08), 0 16px 36px rgba(16,36,47,.12);
}}
@media (prefers-color-scheme: dark) {{
    :root {{
        --bg: #0b1520;
        --surface: #12202e;
        --ink: #e8eff5;
        --muted: #8ea2b4;
        --line: #1f3244;
        --accent-ink: #ffa94d;
        --shadow: 0 1px 2px rgba(0,0,0,.4), 0 8px 24px rgba(0,0,0,.3);
        --shadow-hover: 0 2px 4px rgba(0,0,0,.5), 0 16px 36px rgba(0,0,0,.45);
    }}
}}
* {{ box-sizing: border-box; }}
html {{ scroll-behavior: smooth; }}
body {{
    margin: 0;
    font-family: "Be Vietnam Pro", system-ui, -apple-system, "Segoe UI", Roboto, Arial, sans-serif;
    background: var(--bg);
    color: var(--ink);
    line-height: 1.65;
    -webkit-font-smoothing: antialiased;
}}
a {{ color: inherit; }}
:focus-visible {{ outline: 3px solid var(--accent); outline-offset: 3px; border-radius: 6px; }}

/* ---------- Header ---------- */
.hero {{
    background:
        radial-gradient(900px 300px at 85% -10%, rgba(240,138,36,.28), transparent 60%),
        linear-gradient(135deg, var(--brand) 0%, var(--brand-2) 100%);
    color: #fff;
    padding: 64px 20px 110px;
}}
.hero-inner {{ max-width: 1120px; margin: 0 auto; }}
.hero h1 {{
    margin: 0 0 14px;
    font-size: clamp(32px, 5.5vw, 56px);
    font-weight: 800;
    line-height: 1.1;
    letter-spacing: -0.02em;
    max-width: 18ch;
}}
.hero p {{
    margin: 0;
    max-width: 56ch;
    font-size: clamp(16px, 2vw, 19px);
    color: rgba(255,255,255,.82);
}}

/* ---------- Search ---------- */
.search-wrap {{
    max-width: 1120px;
    margin: -34px auto 0;
    padding: 0 20px;
    position: relative;
    z-index: 2;
}}
.search-box {{
    display: flex;
    align-items: center;
    gap: 12px;
    background: var(--surface);
    border: 1px solid var(--line);
    border-radius: 16px;
    padding: 0 18px;
    box-shadow: var(--shadow-hover);
}}
.search-box svg {{ flex: none; color: var(--muted); }}
.search {{
    flex: 1;
    min-width: 0;
    border: 0;
    background: transparent;
    color: var(--ink);
    font: inherit;
    font-size: 17px;
    padding: 18px 0;
}}
.search:focus {{ outline: none; }}
.search-box:focus-within {{ border-color: var(--accent); box-shadow: 0 0 0 4px rgba(240,138,36,.2), var(--shadow-hover); }}
.search::placeholder {{ color: var(--muted); }}

/* ---------- Layout ---------- */
.container {{ max-width: 1120px; margin: 0 auto; padding: 28px 20px 60px; }}
.meta-row {{
    display: flex;
    justify-content: space-between;
    align-items: baseline;
    flex-wrap: wrap;
    gap: 8px;
    margin-bottom: 22px;
    color: var(--muted);
    font-size: 14px;
}}
.meta-row strong {{ color: var(--ink); }}

.posts {{
    display: grid;
    grid-template-columns: repeat(3, 1fr);
    gap: 24px;
}}

/* ---------- Cards ---------- */
.post {{
    display: flex;
    flex-direction: column;
    background: var(--surface);
    border: 1px solid var(--line);
    border-radius: 18px;
    overflow: hidden;
    box-shadow: var(--shadow);
    transition: transform .25s ease, box-shadow .25s ease;
}}
.post:hover {{ transform: translateY(-4px); box-shadow: var(--shadow-hover); }}
.thumb {{ display: block; aspect-ratio: 16 / 10; overflow: hidden; background: var(--line); }}
.thumb img, .thumb-fallback {{
    width: 100%;
    height: 100%;
    object-fit: cover;
    display: block;
    transition: transform .5s ease;
}}
.thumb-fallback {{ background: linear-gradient(135deg, var(--brand), var(--brand-2)); }}
.post:hover .thumb img {{ transform: scale(1.05); }}
.post-content {{ display: flex; flex-direction: column; flex: 1; padding: 20px 22px 22px; min-width: 0; }}
.date {{ color: var(--muted); font-size: 13px; }}
.post h2 {{
    margin: 6px 0 10px;
    font-size: 19px;
    line-height: 1.35;
    font-weight: 700;
    letter-spacing: -0.01em;
}}
.post h2 a {{ text-decoration: none; }}
.post h2 a:hover {{ color: var(--brand-2); }}
.summary {{
    margin: 0 0 16px;
    color: var(--muted);
    font-size: 15px;
    display: -webkit-box;
    -webkit-line-clamp: 3;
    -webkit-box-orient: vertical;
    overflow: hidden;
}}
.read-more {{
    margin-top: auto;
    align-self: flex-start;
    color: var(--accent-ink);
    font-weight: 600;
    font-size: 14px;
    text-decoration: none;
    border-bottom: 2px solid transparent;
}}
.read-more:hover {{ border-bottom-color: currentColor; }}

/* Bài đầu tiên nổi bật (chỉ khi chưa tìm kiếm) */
.posts.idle .post:first-child {{
    grid-column: 1 / -1;
    flex-direction: row;
}}
.posts.idle .post:first-child .thumb {{ flex: 1.2; aspect-ratio: auto; min-height: 320px; }}
.posts.idle .post:first-child .post-content {{ flex: 1; justify-content: center; padding: 36px; }}
.posts.idle .post:first-child h2 {{ font-size: clamp(24px, 3vw, 32px); line-height: 1.2; }}
.posts.idle .post:first-child .summary {{ font-size: 16px; -webkit-line-clamp: 4; }}

.empty {{
    display: none;
    text-align: center;
    padding: 60px 20px;
    color: var(--muted);
}}
.empty strong {{ display: block; color: var(--ink); font-size: 18px; margin-bottom: 6px; }}

/* ---------- Footer ---------- */
footer {{
    border-top: 1px solid var(--line);
    padding: 32px 20px 44px;
    text-align: center;
    color: var(--muted);
    font-size: 14px;
}}
footer p {{ margin: 4px 0; }}
footer a {{ color: var(--accent-ink); font-weight: 600; text-decoration: none; }}
footer a:hover {{ text-decoration: underline; }}

/* ---------- Responsive ---------- */
@media (max-width: 900px) {{
    .posts {{ grid-template-columns: repeat(2, 1fr); }}
}}
@media (max-width: 640px) {{
    .hero {{ padding: 44px 18px 96px; }}
    .posts {{ grid-template-columns: 1fr; gap: 18px; }}
    .posts.idle .post:first-child {{ flex-direction: column; }}
    .posts.idle .post:first-child .thumb {{ min-height: 0; aspect-ratio: 16 / 10; flex: none; }}
    .posts.idle .post:first-child .post-content {{ padding: 20px 22px 22px; }}
}}
@media (prefers-reduced-motion: reduce) {{
    * {{ transition: none !important; scroll-behavior: auto !important; }}
}}
</style>
</head>
<body>
<header class="hero">
    <div class="hero-inner">
        <h1>{html.escape(SITE_NAME)}</h1>
        <p>{html.escape(SITE_DESCRIPTION)}</p>
    </div>
</header>

<div class="search-wrap">
    <label class="search-box">
        <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor"
             stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
            <circle cx="11" cy="11" r="7"></circle><path d="m20 20-3.5-3.5"></path>
        </svg>
        <span style="position:absolute;left:-9999px">Tìm kiếm bài viết</span>
        <input class="search" id="search" type="search" autocomplete="off"
               placeholder="Tìm bài viết theo tiêu đề hoặc nội dung">
    </label>
</div>

<main class="container">
    <div class="meta-row">
        <span id="count"><strong>{len(items)}</strong> bài viết mới nhất</span>
        <span>Cập nhật lần cuối: {updated}</span>
    </div>
    <section class="posts idle" id="posts">
        {''.join(cards)}
    </section>
    <div class="empty" id="empty">
        <strong>Không tìm thấy bài viết nào</strong>
        Hãy thử từ khóa ngắn hơn hoặc kiểm tra lại chính tả.
    </div>
</main>

<footer>
    <p>
        <a href="{MAIN_SITE_URL}/">Truy cập tieushare.com</a>
        &nbsp;·&nbsp;
        <a href="./feed.xml">Đăng ký RSS</a>
    </p>
    <p>© {year} Tiểu Share. Bài viết gốc thuộc về tieushare.com.</p>
</footer>

<script>
(function() {{
    const input = document.getElementById("search");
    const posts = document.getElementById("posts");
    const empty = document.getElementById("empty");
    const count = document.getElementById("count");
    const cards = Array.from(posts.querySelectorAll(".post"));
    const total = cards.length;

    function normalize(s) {{
        return s.toLowerCase().normalize("NFD").replace(/[\\u0300-\\u036f]/g, "").replace(/đ/g, "d");
    }}
    const index = cards.map(c => normalize(c.innerText));

    input.addEventListener("input", function() {{
        const q = normalize(this.value.trim());
        let shown = 0;
        cards.forEach((card, i) => {{
            const match = !q || index[i].includes(q);
            card.style.display = match ? "" : "none";
            if (match) shown++;
        }});
        posts.classList.toggle("idle", !q);
        empty.style.display = shown ? "none" : "block";
        count.innerHTML = q
            ? "<strong>" + shown + "</strong> kết quả"
            : "<strong>" + total + "</strong> bài viết mới nhất";
    }});
}})();
</script>
</body>
</html>"""


# ---------------------------------------------------------------------------
# feed.xml và sitemap.xml
# ---------------------------------------------------------------------------
ATOM_NS = "http://www.w3.org/2005/Atom"


def generate_feed(items):
    ET.register_namespace("atom", ATOM_NS)

    root = ET.Element("rss", version="2.0")
    channel = ET.SubElement(root, "channel")

    ET.SubElement(channel, "title").text = SITE_TITLE
    ET.SubElement(channel, "link").text = PAGE_URL
    ET.SubElement(channel, "description").text = SITE_DESCRIPTION
    ET.SubElement(channel, "language").text = "vi"
    ET.SubElement(channel, "lastBuildDate").text = format_datetime(
        datetime.now(timezone.utc)
    )
    ET.SubElement(channel, f"{{{ATOM_NS}}}link", {
        "href": f"{SITE_URL}/feed.xml",
        "rel": "self",
        "type": "application/rss+xml",
    })

    for item in items:
        element = ET.SubElement(channel, "item")
        ET.SubElement(element, "title").text = item["title"]
        ET.SubElement(element, "link").text = item["link"]
        ET.SubElement(element, "guid").text = item["link"]
        ET.SubElement(element, "description").text = item["description"]

        if item["pub_date"]:
            ET.SubElement(element, "pubDate").text = item["pub_date"]

    return ET.ElementTree(root)


def generate_sitemap():
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url>
    <loc>{html.escape(PAGE_URL)}</loc>
    <lastmod>{today}</lastmod>
    <changefreq>daily</changefreq>
  </url>
</urlset>
"""


def main():
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    print("Fetching Blogger RSS...")
    items = fetch_rss()

    print(f"Found {len(items)} articles.")

    if not items:
        raise RuntimeError("No articles found. Keeping deployment from failing.")

    with open(f"{OUTPUT_DIR}/index.html", "w", encoding="utf-8") as file:
        file.write(generate_html(items))

    generate_feed(items).write(
        f"{OUTPUT_DIR}/feed.xml",
        encoding="utf-8",
        xml_declaration=True
    )

    with open(f"{OUTPUT_DIR}/sitemap.xml", "w", encoding="utf-8") as file:
        file.write(generate_sitemap())

    print("Website generated successfully.")


if __name__ == "__main__":
    main()
