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
W("# Corpus des banques centrales — documentation détaillée\n")
W("*Référence des données — **régénérée automatiquement** depuis `data/manifest.jsonl` "
  "via `gen_corpus.py`.*\n")
W("> Présentation non-technique : `PRESENTATION.md` · Couverture/audit : `DISCOVERY_AUDIT.md` "
  "· Ingestion RAG : `INGESTION_RAG.md` · Architecture : `ARCHITECTURE_REVIEW.md`\n")

W("\n## 1. Vue d'ensemble\n")
W("| Métrique | Valeur |")
W("|---|---|")
W(f"| Documents | **{total:,}** |".replace(",", " "))
W(f"| Banques centrales | **{nbanks}** (sur 63 ciblées) |")
W(f"| Période | **{min(allyears)} → {max(allyears)}** |")
W(f"| Langue | **{', '.join(lang)}** |")
W(f"| Taille | **{size}** |")
W(f"| Index | `data/manifest.jsonl` (1 ligne JSON / document) |")
W("\n**Principe :** documents officiels de première main uniquement (site de la banque, "
  "`bis.org` pour les discours, ou IDEAS/RePEc → PDF de l'éditeur pour les working papers). "
  "Dédup par `doc_id` + `sha256`.\n")

W("\n## 2. Schéma des données (`manifest.jsonl`)\n")
W("| Champ | Usage RAG |")
W("|---|---|")
for f, u in [("bank_code", "**filtre** banque/pays"), ("doc_type", "**filtre** type"),
             ("title", "titre / embedding"), ("date / year", "**filtre temporel**"),
             ("pdf_url", "**citation** (source officielle)"), ("source_url", "page d'origine"),
             ("provenance", "`bis_index` / `bank_site` / `repec_discovery`"),
             ("mime_type", "`application/pdf` / `text/html`"),
             ("local_path", "**chemin du fichier** à lire"), ("html_path", "HTML source (si rendu)"),
             ("sha256", "dédup / intégrité"), ("doc_id", "clé primaire stable")]:
    W(f"| `{f}` | {u} |")
W("\nLayout : `data/raw/<bank>/<doc_type>/<year>/<doc_id>.<pdf|html>`\n")

W("\n## 3. Inventaire par type de document\n")
W("| Type | Description | Nombre | Source |")
W("|---|---|---:|---|")
desc = {"A1": ("Décisions de taux", "sites banques"),
        "A2": ("Communiqués de politique monétaire", "sites banques"),
        "A3": ("Minutes / comptes-rendus", "sites banques"),
        "B1": ("Conférences de presse (Q&A)", "BCE/Fed"),
        "C1": ("Discours", "index BIS"),
        "C2": ("Interviews / op-eds / testimonies", "BCE"),
        "D1": ("Working papers", "RePEc/IDEAS → PDF éditeur"),
        "D2": ("Occasional / discussion papers", "RePEc/IDEAS"),
        "D3": ("Blog / lettres de recherche", "sites banques"),
        "E1": ("Rapports politique mon. / inflation", "sites banques"),
        "E2": ("Stabilité financière (FSR, macropru.)", "sites banques"),
        "E3": ("Rapports annuels / de convergence", "BCE"),
        "E4": ("Bulletins économiques", "BCE"),
        "F1": ("Projections / prévisions (SEP)", "Fed"),
        "G2": ("Enquêtes / publications statistiques", "BCE")}
for t in TYPES_PRESENT:
    de, s = desc.get(t, (t, "—"))
    W(f"| **{t}** | {de} | {bytype[t]:,} | {s} |".replace(",", " "))
W(f"\n**Provenance :** " + " · ".join(f"`{k}` {v:,}".replace(",", " ")
                                      for k, v in prov.most_common()) + "\n")

W("\n## 4. Inventaire par pays\n")
W(f"{nbanks} banques, triées par volume. Colonnes = documents par type.\n")
W("| # | Code | Banque centrale | Total | Années | " + " | ".join(TYPES_PRESENT) + " |")
W("|---:|---|---|---:|---|" + "|".join("---:" for _ in TYPES_PRESENT) + "|")
for i, (c, n) in enumerate(tot_by_bank.most_common(), 1):
    ys = bankyears[c]; yr = f"{min(ys)}-{max(ys)}" if ys else "—"
    cells = " | ".join(str(bt[c][t]) if bt[c][t] else "—" for t in TYPES_PRESENT)
    W(f"| {i} | `{c}` | {names.get(c, c)} | {n:,} | {yr} | {cells} |".replace(",", " "))
W("\n### Banques ciblées absentes (0 doc)")
for c in missing:
    W(f"- `{c}` {names[c]}")
W("\n> `pe` (espagnol) et `vn` (vietnamien) : absences réelles confirmées — pas de version "
  "EN indexée par la BIS. Voir `DISCOVERY_AUDIT.md`.\n")

W("\n## 5. Couverture temporelle (documents par tranche de 5 ans)\n")
decs = sorted({(y // 5) * 5 for y in allyears})
W("| Type | " + " | ".join(f"{d}" for d in decs) + " |")
W("|---|" + "|".join("---:" for _ in decs) + "|")
for t in TYPES_PRESENT:
    W(f"| {t} | " + " | ".join(str(ty_dec[t].get(d, 0) or "—") for d in decs) + " |")

W("\n## 6. Notes d'ingestion RAG (voir `INGESTION_RAG.md` pour le détail)\n")
W("- Filtres : `bank_code`, `doc_type`, `year`, `provenance`.")
W("- Citation : `pdf_url` + `title` + `date`. Chargement : `local_path`.")
W("- Dédup déjà faite (`sha256`). Exclure les `.html` orphelins et `.DS_Store`.")
W(f"- Composition : discours {100*bytype['C1']//total}% · working papers "
  f"{100*(bytype['D1']+bytype['D2'])//total}% · autres. Pondérer le retrieval selon le besoin.")

W("\n## 7. Complétude RePEc (D1/D2) & gaps connus\n")
W("Working papers récupérés vs cible découvrable (IDEAS, pagination complète) :\n")
target = {'us':4198,'ecb':3658,'ca':1360,'it':1211,'gb':1128,'es':1088,'fr':1005,
          'de':691,'au':536,'jp':405,'se':391,'ch':318,'nl':200}
gg = sum(bt[b]['D1'] + bt[b]['D2'] for b in target); tg = sum(target.values())
W(f"**Total : {gg:,} / {tg:,} ({100*gg//tg}%).**".replace(",", " ") +
  " Gaps : `se` host mort, + entrées *abstract-only* sans PDF "
  "(non récupérables). Détail + remèdes : `DISCOVERY_AUDIT.md`.\n")

W("\n## 8. Reproductibilité\n")
W("```bash")
W("python -m cb_corpus bis-sitemap --download     # discours C1")
W("python -m cb_corpus repec --download           # working papers D1/D2 (paginé, daté)")
W("python -m cb_corpus discover --download --rounds 3   # natif A/B/E/F par banque")
W("python3.13 gen_corpus.py                        # régénère ce fichier")
W("```")
W("Idempotent (`doc_id`+`sha256`), convergence (`--rounds`), échecs tracés dans "
  "`data/discovery_errors.jsonl`.\n")

open("CORPUS.md", "w").write("\n".join(out) + "\n")
print(f"CORPUS.md régénéré : {total} docs, {nbanks} banques, {size}, "
      f"{len(open('CORPUS.md').read())} octets")
