"""Build phone-friendly, clickable PDFs from a city's output CSVs.

    python3 tools/make_pdfs.py            # London  -> output/london/pdf/
    python3 tools/make_pdfs.py bangkok    # any city -> output/<city>/pdf/

<city>_food_emails.pdf (tap an email to open Mail) and <city>_food_websites.pdf (tap to open the
browser). Each starts with a tappable index and has PDF bookmarks per section. London is grouped by
borough; cities with an English-priority score are grouped High / Medium / Low English priority, with
the district on each entry.
"""

import csv
import os
import sys
from collections import defaultdict
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import inch
from reportlab.platypus import (BaseDocTemplate, Frame, PageBreak, PageTemplate, Paragraph, Spacer,
                                KeepTogether)

ROOT = os.path.join(os.path.dirname(__file__), "..")
PAGE = (4.8 * inch, 8.4 * inch)  # roughly a phone screen, so text is readable without zooming
M = 0.3 * inch

BLUE = colors.HexColor("#0a58ca")
GREY = colors.HexColor("#555555")
title = ParagraphStyle("t", fontName="Helvetica-Bold", fontSize=18, leading=22, spaceAfter=6)
sub = ParagraphStyle("s", fontName="Helvetica", fontSize=10, leading=13, textColor=GREY, spaceAfter=10)
h1 = ParagraphStyle("h1", fontName="Helvetica-Bold", fontSize=16, leading=20, spaceBefore=4, spaceAfter=8)
idx = ParagraphStyle("i", fontName="Helvetica", fontSize=12, leading=19)
name = ParagraphStyle("n", fontName="Helvetica-Bold", fontSize=11.5, leading=14)
link = ParagraphStyle("l", fontName="Helvetica", fontSize=11.5, leading=15)
meta = ParagraphStyle("m", fontName="Helvetica", fontSize=9, leading=11.5, textColor=GREY, spaceAfter=9)


def anchor(b):
    return "b_" + "".join(c for c in b if c.isalnum())


class Doc(BaseDocTemplate):
    def __init__(self, path, heading):
        super().__init__(path, pagesize=PAGE, leftMargin=M, rightMargin=M, topMargin=M, bottomMargin=M,
                         title=heading, author="London food venue contacts")
        frame = Frame(M, M + 12, PAGE[0] - 2 * M, PAGE[1] - 2 * M - 12, id="f")
        self.addPageTemplates([PageTemplate("p", [frame], onPage=self.footer)])

    @staticmethod
    def footer(canvas, doc):
        canvas.setFont("Helvetica", 8)
        canvas.setFillColor(GREY)
        canvas.drawCentredString(PAGE[0] / 2, M * 0.6, f"{doc.page}")
        # tap the footer to jump back to the borough index
        canvas.linkRect("", "index", (M, 0, PAGE[0] - M, M + 6), relative=0, thickness=0)

    def afterFlowable(self, f):
        key = getattr(f, "_bookmark", None)
        if key:
            self.canv.bookmarkPage(key)
            self.canv.addOutlineEntry(f._label, key, level=0)


PRIORITIES = ["High", "Medium", "Low"]


def build(kind, city="london"):
    base = os.path.join(ROOT, "output", city)
    rows = list(csv.DictReader(open(os.path.join(base, f"{city}_food_{kind}.csv"), encoding="utf-8")))
    ranked = bool(rows) and "english_priority" in rows[0]
    by_b = defaultdict(list)
    for r in rows:
        by_b[r["english_priority"] + " English priority" if ranked else r["borough"]].append(r)
    boroughs = [p + " English priority" for p in PRIORITIES if p + " English priority" in by_b] if ranked \
        else sorted(by_b)
    label = {"emails": "Emails", "websites": "Websites", "social": "Facebook & Instagram"}[kind]
    place = {"london": "London"}.get(city, city.replace("_", " ").title())
    os.makedirs(os.path.join(base, "pdf"), exist_ok=True)
    path = os.path.join(base, "pdf", f"{city}_food_{kind}.pdf")
    doc = Doc(path, f"{place} Food Venue {label}")

    story = [Paragraph(f"{place} Food Venue {label}", title),
             Paragraph(f"{len(rows):,} {label.lower()}"
                       + (", most English-friendly first. " if ranked else f" across {len(boroughs)} areas. ") +
                       f"Tap {'an email to write to' if kind == 'emails' else 'a link to open'} the venue. "
                       + ("Venues with no email or website; their own page as listed on the map. " if kind == "social" else "")
                       + 
                       f"Tap a section below to jump to it; tap a page number to come back here.", sub)]
    ix = Paragraph("", idx)
    ix._bookmark, ix._label = "index", "Borough index"
    story.append(ix)
    for b in boroughs:
        story.append(Paragraph(f'<a href="#{anchor(b)}" color="#0a58ca">{escape(b)}</a>'
                               f' <font color="#777777" size="9">({len(by_b[b]):,})</font>', idx))

    for b in boroughs:
        story.append(PageBreak())
        head = Paragraph(f'<a name="{anchor(b)}"/>{escape(b)} <font size="10" color="#777777">'
                         f'{len(by_b[b]):,}</font>', h1)
        head._bookmark, head._label = anchor(b), b
        story.append(head)
        order = (lambda r: (r["borough"], r["name"].lower())) if ranked else \
            (lambda r: (r.get("area") or "~", r["name"].lower()))
        for r in sorted(by_b[b], key=order):
            if kind == "emails":
                target, shown = "mailto:" + r["email"], r["email"]
            elif kind == "social":
                target = r["social_url"]
                shown = target.split("://", 1)[-1].removeprefix("www.").rstrip("/")
            else:
                target = r["website"]
                shown = target.split("://", 1)[-1].removeprefix("www.").rstrip("/")
                if len(shown) > 48:
                    shown = shown[:45] + "..."
            bits = [r.get("district") or r.get("area"), r.get("categories"), r.get("phone")]
            if ranked and r.get("site_language"):
                bits.append(f"site: {r['site_language']}")
            if r.get("name_local"):
                bits.insert(0, r["name_local"])
            if kind == "websites" and r.get("email"):
                bits.append(r["email"])
            if r.get("locations") and r["locations"] not in ("", "1"):
                bits.append(f"{r['locations']} branches")
            story.append(KeepTogether([
                Paragraph(escape(r["name"]), name),
                Paragraph(f'<link href="{escape(target)}" color="#0a58ca"><u>{escape(shown)}</u></link>', link),
                Paragraph(escape(" · ".join(x for x in bits if x)), meta),
            ]))
    doc.build(story)
    return path, len(rows)


if __name__ == "__main__":
    city = sys.argv[1] if len(sys.argv) > 1 else "london"
    for k in ("emails", "websites", "social"):
        if not os.path.exists(os.path.join(ROOT, "output", city, f"{city}_food_{k}.csv")):
            continue
        p, n = build(k, city)
        print(p, n, f"{os.path.getsize(p) / 1e6:.1f} MB")
