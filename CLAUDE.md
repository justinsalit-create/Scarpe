# Instructions for Claude sessions in this repo

This repo collects food-venue contact emails and websites, city by city, for one client.
**Read `PLAYBOOK.md` before doing anything.** It has the client's standing rules (two separate files,
nothing guessed, no duplicates, no closed venues, no social media links, clickable iPhone PDFs), the
sources and method, and the new-city checklist.

- Each city is separate: `cities/<slug>.json`, `output/<slug>/`, `cache/<slug>/`.
- New city: add `cities/<slug>.json`, test one district, then `./city_all.sh <slug>` in the background.
- Tests: `python3 -m unittest`. Run them before every commit.
- Never put `python3 -m londonfood` in a `pkill -f` pattern: it matches and kills your own shell.
