# Merge of Muse's Pattaya & Jomtien social venues (2026-10-13)

Source: `data/partner/pattaya_muse_2026-10-13.csv` (from hmm-partner-finder
`muse-claude-handoffs/results/pattaya-jomtien/`), merged by `londonfood/partner.py` under the rules in
`muse-claude-handoffs/tasks/2026-10-13-pattaya-jomtien-social-venues.md`.

| | Count |
|---|---|
| Partner rows | 428 |
| Excluded: permanently closed | 2 (Cafe Pitini Pattaya, Mantra Restaurant and Bar) |
| Excluded: not a food venue | 2 (Copacabana Coral Reef, The Riviera) |
| Excluded: Chon Buri city (outside Pattaya/Jomtien, kept separate) | 15 |
| **Overlap with our run**: matched by email / Instagram / Facebook, or name + website/phone | **120** |
| **Overlap**: matched by unique full name (partner rows have no location) | **64** |
| New venues added | 225 |
| Kept, possible duplicate (name shared by several of ours) | 1 (Honey Pizza Pattaya) |

Effect on the Pattaya files (before → after; also includes a crawler fix that stopped dropping venues
whose listed sub-page, e.g. `/home`, was gone while the site was live):

| File | Before | After | New entries | From Muse |
|---|---|---|---|---|
| Emails | 2,611 | 2,667 | 56 | 42 |
| Websites | 809 | 901 | 93 | 59 |
| Facebook/Instagram-only | 5,101 | 5,128 | | 50 Muse rows |
| Master venues | 8,788 | 8,991 | | 190 Muse-only + 148 merged |

Of the partner's 68 unique emails: 51 are a venue's main email, 16 are kept as extra addresses on venues
that already had one, 2 failed validation (rsvn@gtpattayabeach.com, reserve@uapattaya.com: domain doesn't
exist), and 2 belong to venues whose website is gone (oasispattaya.com returns 404; theviewpattaya.com is
parked, which the partner also flagged), so they're excluded as likely closed.
Temporarily closed rows (Edge, PIPPA) are kept with a note. "Five Star J" isn't in the file.
Partner websites that are Facebook / Linktree pages are not counted as websites (client rule); those venues
go to the Facebook/Instagram file when they have no email.
