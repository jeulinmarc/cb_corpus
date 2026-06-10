"""Regenerate CORPUS.md from data/manifest.jsonl (reproducible data reference).

Run:  python3.13 gen_corpus.py
"""
import json, collections, subprocess
from datetime import date
from cb_corpus.banks import BIS_63

names = {b.code: b.name for b in BIS_63}
rows = [json.loads(l) for l in open("data/manifest.jsonl")]

bt = collections.defaultdict(collections.Counter)
bytype = collections.Counter(); prov = collections.Counter(); lang = collections.Counter()
bankyears = collections.defaultdict(list)
ty_dec = collections.defaultdict(collections.Counter)
for d in rows:
    c, t = d["bank_code"], d["doc_type"]
    bt[c][t] += 1; bytype[t] += 1
    prov[d.get("provenance", "?")] += 1; lang[d.get("language", "?")] += 1
    if d.get("year"):
        bankyears[c].append(d["year"]); ty_dec[t][(d["year"] // 5) * 5] += 1

total = len(rows)
nbanks = len(bt)
allyears = [y for ys in bankyears.values() for y in ys]
size = subprocess.run(["du", "-sh", "data/raw"], capture_output=True, text=True).stdout.split()[0]
tot_by_bank = collections.Counter({c: sum(v.values()) for c, v in bt.items()})
TYPES_PRESENT = sorted(t for t in bytype if bytype[t])   # every type actually present, A1..G2
missing = [b.code for b in BIS_63 if b.code not in bt]

out = []
W = out.append
W("# Central bank corpus — detailed documentation\n")
W("*Data reference — **auto-regenerated** from `data/manifest.jsonl` "
  "via `gen_corpus.py`.*\n")
W("> Non-technical presentation: `PRESENTATION.md` · Coverage/audit: `DISCOVERY_AUDIT.md` "
  "· RAG ingestion: `INGESTION_RAG.md` · Architecture: `ARCHITECTURE.md`\n")

W("\n## 1. Overview\n")
W("| Metric | Value |")
W("|---|---|")
W(f"| Documents | **{total:,}** |".replace(",", " "))
W(f"| Central banks | **{nbanks}** (out of 63 targeted) |")
W(f"| Period | **{min(allyears)} → {max(allyears)}** |")
W(f"| Language | **{', '.join(lang)}** |")
W(f"| Size | **{size}** |")
W(f"| Index | `data/manifest.jsonl` (1 JSON line / document) |")
W("\n**Principle:** first-hand official documents only (the bank's site, "
  "`bis.org` for speeches, or IDEAS/RePEc → publisher PDF for working papers). "
  "Dedup by `doc_id` + `sha256`.\n")

W("\n## 2. Data schema (`manifest.jsonl`)\n")
W("| Field | RAG use |")
W("|---|---|")
for f, u in [("bank_code", "bank/country **filter**"), ("doc_type", "type **filter**"),
             ("title", "title / embedding"), ("date / year", "**temporal filter**"),
             ("pdf_url", "**citation** (official source)"), ("source_url", "origin page"),
             ("provenance", "`bis_index` / `bank_site` / `repec_discovery`"),
             ("mime_type", "`application/pdf` / `text/html`"),
             ("local_path", "**file path** to read"), ("html_path", "HTML source (if rendered)"),
             ("sha256", "dedup / integrity"), ("doc_id", "stable primary key")]:
    W(f"| `{f}` | {u} |")
W("\nLayout: `data/raw/<bank>/<doc_type>/<year>/<doc_id>.<pdf|html>`\n")

W("\n## 3. Inventory by document type\n")
W("| Type | Description | Count | Source |")
W("|---|---|---:|---|")
desc = {"A1": ("Rate decisions", "bank sites"),
        "A2": ("Monetary policy statements", "bank sites"),
        "A3": ("Minutes / accounts", "bank sites"),
        "B1": ("Press conferences (Q&A)", "ECB/Fed"),
        "C1": ("Speeches", "BIS index"),
        "C2": ("Interviews / op-eds / testimonies", "ECB"),
        "D1": ("Working papers", "RePEc/IDEAS → publisher PDF"),
        "D2": ("Occasional / discussion papers", "RePEc/IDEAS"),
        "D3": ("Blog / research letters", "bank sites"),
        "E1": ("Monetary policy / inflation reports", "bank sites"),
        "E2": ("Financial stability (FSR, macropru.)", "bank sites"),
        "E3": ("Annual / convergence reports", "ECB"),
        "E4": ("Economic bulletins", "ECB"),
        "F1": ("Projections / forecasts (SEP)", "Fed"),
        "G2": ("Surveys / statistical publications", "ECB")}
for t in TYPES_PRESENT:
    de, s = desc.get(t, (t, "—"))
    W(f"| **{t}** | {de} | {bytype[t]:,} | {s} |".replace(",", " "))
W(f"\n**Provenance:** " + " · ".join(f"`{k}` {v:,}".replace(",", " ")
                                      for k, v in prov.most_common()) + "\n")

W("\n## 4. Inventory by country\n")
W(f"{nbanks} banks, sorted by volume. Columns = documents per type.\n")
W("| # | Code | Central bank | Total | Years | " + " | ".join(TYPES_PRESENT) + " |")
W("|---:|---|---|---:|---|" + "|".join("---:" for _ in TYPES_PRESENT) + "|")
for i, (c, n) in enumerate(tot_by_bank.most_common(), 1):
    ys = bankyears[c]; yr = f"{min(ys)}-{max(ys)}" if ys else "—"
    cells = " | ".join(str(bt[c][t]) if bt[c][t] else "—" for t in TYPES_PRESENT)
    W(f"| {i} | `{c}` | {names.get(c, c)} | {n:,} | {yr} | {cells} |".replace(",", " "))
W("\n### Targeted banks absent (0 docs)")
for c in missing:
    W(f"- `{c}` {names[c]}")
W("\n> `pe` (Spanish) and `vn` (Vietnamese): confirmed real absences — no EN version "
  "indexed by the BIS. See `DISCOVERY_AUDIT.md`.\n")

W("\n## 5. Temporal coverage (documents per 5-year bucket)\n")
decs = sorted({(y // 5) * 5 for y in allyears})
W("| Type | " + " | ".join(f"{d}" for d in decs) + " |")
W("|---|" + "|".join("---:" for _ in decs) + "|")
for t in TYPES_PRESENT:
    W(f"| {t} | " + " | ".join(str(ty_dec[t].get(d, 0) or "—") for d in decs) + " |")

W("\n## 6. RAG ingestion notes (see `INGESTION_RAG.md` for detail)\n")
W("- Filters: `bank_code`, `doc_type`, `year`, `provenance`.")
W("- Citation: `pdf_url` + `title` + `date`. Loading: `local_path`.")
W("- Dedup already done (`sha256`). Exclude orphan `.html` and `.DS_Store`.")
W(f"- Composition: speeches {100*bytype['C1']//total}% · working papers "
  f"{100*(bytype['D1']+bytype['D2'])//total}% · others. Weight retrieval as needed.")

W("\n## 7. RePEc completeness (D1/D2) & known gaps\n")
W("Working papers retrieved vs discoverable target (IDEAS, full pagination):\n")
target = {'us':4198,'ecb':3658,'ca':1360,'it':1211,'gb':1128,'es':1088,'fr':1005,
          'de':691,'au':536,'jp':405,'se':391,'ch':318,'nl':200}
gg = sum(bt[b]['D1'] + bt[b]['D2'] for b in target); tg = sum(target.values())
W(f"**Total: {gg:,} / {tg:,} ({100*gg//tg}%).**".replace(",", " ") +
  " Gaps: `se` dead host, + *abstract-only* entries with no PDF "
  "(unrecoverable). Detail + remedies: `DISCOVERY_AUDIT.md`.\n")

W("\n## 8. Reproducibility\n")
W("```bash")
W("python -m cb_corpus bis-sitemap --download     # C1 speeches")
W("python -m cb_corpus repec --download           # D1/D2 working papers (paginated, dated)")
W("python -m cb_corpus discover --download --rounds 3   # native A/B/E/F per bank")
W("python3.13 gen_corpus.py                        # regenerate this file")
W("```")
W("Idempotent (`doc_id`+`sha256`), convergence (`--rounds`), failures traced in "
  "`data/discovery_errors.jsonl`.\n")

open("CORPUS.md", "w").write("\n".join(out) + "\n")
print(f"CORPUS.md regenerated: {total} docs, {nbanks} banks, {size}, "
      f"{len(open('CORPUS.md').read())} bytes")
