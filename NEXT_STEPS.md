# cb_corpus — NEXT STEPS

Audit de couverture au 2026-06-02 et plan de travail pour combler les manques.

État de référence : `central_bank_corpus_inventory.md` (taxonomie A–G et volumes attendus).

## 1. État actuel du corpus

### Volumes
- **17 585 records** dans `data/manifest.jsonl`.
- **~5.0 GB** sur disque (raw PDFs + HTML siblings préservés).
- **Plage temporelle** : 1997-01 → 2026-06.
- **Couverture banques** : 58 / 63 (manquantes : `lv`, `lt`, `lu`, `pe`, `vn`).
- **Couverture types** : 5 / 16 types A–F.

### Breakdown par type de document
| Type | Description | Records | Source(s) |
|---|---|---|---|
| C1 | Discours | 16 442 | BIS sitemap (96.4 % des records) |
| E1 | MP / Inflation reports | 658 | RBA SMP (583), BoC MPR (75) |
| A3 | Minutes / accounts | 375 | Fed FOMC (149), RBA (120), ECB (74), BoJ (32) |
| E4 | Economic Bulletin | 89 | ECB (Bulletin trimestriel) |
| F1 | Staff projections | 21 | Fed SEP |

### Types manquants
A1, A2, A4, B1, B2, C2, D1, D2, D3, E2, E3 — **11 types non couverts sur 16**.

### Politique de format
- Tous les records sur disque sont en `application/pdf`.
- 118 records ont également le HTML source préservé (champ `html_path` du manifest) — politique "keep both" en place depuis le 2026-06-02.
- 25 records ont perdu leur HTML d'origine (politique antérieure qui supprimait) — irrécupérables sauf re-téléchargement.

---

## 2. Gaps prioritaires et plan de comblement

### P0 — Quick wins (forte valeur, effort minimal)

#### 2.1. Bug RePEc : 0 working paper récupéré ⚠️

**Symptôme** : `SERIES` dans `cb_corpus/sources/repec.py` configure 13 séries (ECB, Fed, BoE, Bundesbank, BdI, BdE, BdF, BoC, BoJ, SNB, Riksbank, DNB, RBA). Le `discover` non-C1 a tourné mais **0 record D1/D2 sauvegardé**.

**Cause confirmée par sondage live** :
- La fetch de la series page fonctionne (`https://ideas.repec.org/s/ecb/ecbwps.html` → 72 KB, 200 paper URLs trouvés).
- La fetch du paper page fonctionne (88 KB).
- Le PDF URL est présent dans le HTML : `https://www.ecb.europa.eu//pub/pdf/scpwps/ecbwp722.pdf`.
- MAIS il est dans un `<INPUT type="radio" name="url" value="...">`, **pas dans un `<a href>`**.
- Notre parser `extract_pdf()` n'examine que les `<a href>` → 0 capture.

**Markup exact rencontré** :
```html
<INPUT TYPE="radio" NAME="url" VALUE="https://www.ecb.europa.eu//pub/pdf/scpwps/ecbwp722.pdf" checked>
<B>File URL:</B> <span style="word-break:break-all">https://www.ecb.europa.eu//pub/pdf/scpwps/ecbwp722.pdf</span>
```

**Fix** dans `cb_corpus/sources/repec.py:extract_pdf()` :
```python
def extract_pdf(paper_html, bank_homepage=None):
    soup = BeautifulSoup(paper_html, "lxml")
    candidates: list[str] = []
    # 1. <a href> (rare on IDEAS today but kept for compat).
    for a in soup.find_all("a", href=True):
        ...
    # 2. NEW: <input name="url" value="..."> — IDEAS form pattern.
    for inp in soup.find_all("input", attrs={"name": "url"}):
        v = inp.get("value", "")
        if v.startswith("http"):
            candidates.append(v)
    # 3. NEW fallback: regex on the raw HTML for any absolute .pdf URL.
    import re
    for m in re.finditer(r'https?://[^\s"\'<>]+?\.pdf', paper_html):
        candidates.append(m.group(0))
    # Dedupe + apply preference order.
    ...
```

**Volume attendu après fix** : 5 000 – 15 000 working papers sur les 13 séries (ECB seul ~3 500 WPs, Fed FEDS ~2 000, BoE ~1 000, etc.). Pages IDEAS limitent souvent à 200 derniers — il faudra peut-être paginer pour les séries historiques.

**Tests à ajouter** dans `tests/test_framework.py` :
- Fixture HTML avec `<input name="url" value="...pdf">` → `extract_pdf` doit le trouver.
- Fixture avec PDF en `<a href>` ET en `<input>` → vérifier l'ordre de préférence.

**Effort estimé** : 30 min code + tests, plus un re-run discover de ~30 min.

#### 2.2. A1 / A2 — Rate decisions et policy statements

**Constat** : aucun couvert. Critique pour l'analyse de politique monétaire.

**Stratégie** : ajouter des entrées TOML pour les 5-10 banques majeures.

**Patterns à investiguer** :

| Banque | URL listing | Pattern attendu |
|---|---|---|
| Fed (us) | `https://www.federalreserve.gov/newsevents/pressreleases/monetary{yyyy}{mm}{dd}a.htm` | Au lieu d'un listing, le calendrier `fomccalendars.htm` a aussi les statements PDF (`fomcstatementYYYYMMDD.pdf` ?) à confirmer |
| ECB (ecb) | `https://www.ecb.europa.eu/press/pr/date/{year}/html/index_include.en.html` | Pattern lazy-load comme accounts. URLs comme `/press/pr/date/2026/html/ecb.mp260430~xx.en.html` (déjà aperçu lors du debug ECB) |
| BoE (gb) | `https://www.bankofengland.co.uk/monetary-policy-summary-and-minutes/{year}/...` | Page index par année avec liens PDF |
| RBA (au) | Déjà partiel via A3 minutes ; statements séparés sur `/media-releases/{year}/` | À auditer |
| BoJ (jp) | `https://www.boj.or.jp/en/mopo/mpmdeci/state_{year}/index.htm` | Pattern numérique mensuel |
| SNB (ch) | `https://www.snb.ch/public/seo/sitemap.xml` filtre `medienmitteilungen` (déjà en TOML pour A2 — à vérifier qu'il livre) |

**Action concrète** :
1. Sonder chaque page de listing (un `curl` par banque, comme on a fait pour ECB lors du debug).
2. Identifier le pattern de date dans l'URL.
3. Ajouter dans `cb_corpus/banks_sources.toml` une section `[<code>.listing]` ou `[<code>.sitemap]`.
4. Run `discover --types A1,A2 --banks us,ecb,gb,au,jp,ch --since 2010-01-01 --download`.

**Effort estimé** : 2-3 h par banque (sonde + parser + test) × 5-10 banques = 1-2 jours de travail.

#### 2.3. E2 — Financial Stability Reports

**Constat** : 0 records. Pourtant **quasi toutes les BIS-63 publient un FSR** (semi-annuel ou annuel).

**Stratégie** : entrée TOML par banque, regex sur sitemap ou listing page.

**Patterns connus** :

| Banque | URL | Pattern |
|---|---|---|
| Fed (us) | `https://www.federalreserve.gov/publications/financial-stability-report.htm` | `/files/financial-stability-report-{yyyymm}.pdf` |
| ECB | `https://www.ecb.europa.eu/press/financial-stability-publications/fsr/html/index.en.html` | Similaire au pattern accounts (lazy-load year includes) |
| BoE | `https://www.bankofengland.co.uk/financial-stability-report/{year}/...` | Listing par année |
| SNB | Sitemap regex `finanzstabilitaetsbericht` (déjà dans TOML — à valider qu'il livre) |
| BdF | Sitemap regex `stabilite-financiere` (déjà dans TOML pour `fr` — à valider) |
| Bundesbank, Banca d'Italia, BdE, BoC, RBA, BoJ, Riksbank, Norges, etc. — chacun a un FSR annuel |

**Volume attendu** : ~120 banques × ~1-2 FSR/an × 30 ans = 3 000-7 000 records.

**Effort estimé** : 1 jour pour les 10 majors, 2-3 jours pour le long tail.

---

### P1 — Coverage gap (effort modéré)

#### 2.4. E3 — Annual reports

**Constat** : 0 records. Toutes les banques en publient 1/an.

**Stratégie** : pattern URL très souvent du genre `/publications/annual-report/{year}/...pdf`. Souvent dans le sitemap.

**Volume attendu** : ~60 banques × 1/an × 30 ans = ~1 800 records.

**Effort estimé** : 1 jour de patterns par banque (peu profond).

#### 2.5. B1 — Press conference transcripts

**Sources principales** :
- **Fed** : FOMC press conference transcripts depuis 2011, sur `/monetarypolicy/fomcpresconf{yyyymmdd}.htm` (page) → PDF dans la page.
- **ECB** : Press conference transcripts depuis 1998, sur `/press/pressconf/{year}/html/...`.
- **BoE** : Monetary Policy Summary press conferences (depuis 2015 environ).
- **BoJ** : Press conferences du Governor.

**Volume attendu** : ~4 banques × 4-8/an × 15 ans ≈ 240-480 records.

**Effort estimé** : 1-2 jours par banque (transcripts varient en format).

#### 2.6. Discours hors BIS-63 (étendre C1)

**Constat** : `_guess_institution` ne mappe que les 63 banques BIS. BIS indexe ~130 banques (~67 hors-perimeter actuel).

**Action** :
1. Étendre `cb_corpus/banks.py` avec un registre élargi `BIS_130` ou similaire (Mauritius, Egypt, Botswana, Bahamas, etc.).
2. Conserver `BIS_63` comme sous-ensemble "core".
3. Le param `only_banks` dans `BISSpeechIndex.discover` reste utilisable pour filtrer.

**Volume attendu** : ~20 000 discours supplémentaires sur 1997-2026.

**Effort estimé** : 1 jour pour assembler la liste + ajuster la registry.

---

### P2 — Long-tail (effort élevé)

#### 2.7. A4 — Voting records

Limité à BoE, Riksbank, parfois Norges Bank. Adapter dédié par banque, ~1 jour chacun.

#### 2.8. C2 — Interviews / op-eds / testimony

BIS index les met dans le même flux que C1 mais avec mention "interview" dans `og:description`. Une heuristique sur le titre/description pourrait séparer. Pas critique.

#### 2.9. D3 — Economic letters / research blogs

Sources : Fed "Liberty Street Economics", FEDS Notes, ECB Research Bulletin, etc. Très divers, scraping par banque. Faible priorité pour quant.

#### 2.10. Banques absentes (5)

- `lv` Latvijas Banka — `https://www.bank.lv/` — petit volume zone euro.
- `lt` Lietuvos bankas — `https://www.lb.lt/` — idem.
- `lu` BCL — `https://www.bcl.lu/` — idem.
- `pe` BCRP — `https://www.bcrp.gob.pe/` — publie en espagnol, peu via BIS.
- `vn` SBV — `https://www.sbv.gov.vn/` — domaine flaggé `verify=True`, source quasi inactive en anglais.

Adapter générique listing_crawler par banque, ~2 h chacune si la page existe.

---

## 3. Optimisations techniques (orthogonales aux gaps)

### 3.1. Parallélisme pour les futurs runs
Le code actuel fait du sequential per-bank. Pour un re-run complet :
- BIS sitemap : un seul host (bis.org), throttle 0.5s → serial OK.
- discover non-C1 : déjà parallèle host-par-host (`min_delay_seconds` par host).
- retry-html : maintenant en `ThreadPoolExecutor(4)` (cf. `cb_corpus/retry_html.py`).

À étendre : `pipeline.run()` pourrait paralléliser les banques avec un ThreadPoolExecutor également.

### 3.2. Auto-rendering HTML→PDF dans Storage
La nouvelle politique `keep both` est en place. Pour les futurs runs :
- HTML source toujours sauvé (`html_path` dans manifest).
- PDF rendu en sibling si `cfg.html_to_pdf=True` (défaut).
- Si Chrome échoue, le HTML reste comme artifact canonique.

À tester sur des futurs runs ECB ou tout nouveau type qui livre du HTML.

### 3.3. Incrementality
Le manifest dédup par `doc_id` (stable) et `pdf_url`. Un re-run est idempotent et ne re-télécharge rien.

Optimisation possible : pour BIS, court-circuiter aussi la fetch du `.htm` quand l'URL est déjà connue (déjà implémenté via `Storage.is_known_url` passé en `skip_url`).

### 3.4. Bug à surveiller — manifest race
Si plusieurs writers parallèles rewrite le manifest à la fin (cf. retry+convert en parallèle), il y a risque de race. Mitigation actuelle dans `retry_html.py` : re-read frais juste avant write. À étendre à `convert.py` si on le relance en parallèle.

---

## 4. Volumes cibles vs actuels

D'après §5 de `central_bank_corpus_inventory.md` :

| Type | Cible historique | Actuel | Gap |
|---|---|---|---|
| A1+A2 | 25 000–35 000 | 0 | **-100 %** |
| A3 | 6 000–12 000 | 375 | **-94 %** (Fed, ECB, RBA, BoJ uniquement) |
| B | 2 000–4 000 | 0 | **-100 %** |
| C1 | ~40 000 | 16 442 | -59 % (BIS-63 filter) |
| D | 30 000–60 000 | 0 | **-100 %** (bug RePEc) |
| E1 | 3 000–6 000 | 658 | -85 % |
| E2 | 2 000–4 000 | 0 | **-100 %** |
| E3 | 3 000–5 000 | 0 | **-100 %** |

**Total cible** : ~70-120k docs.
**Total actuel** : ~17.5k docs.
**Couverture** : ~15-20 % du potentiel.

---

## 5. Plan d'exécution suggéré

**Sprint 1 (1-2 jours)** — Quick wins
1. Fix `extract_pdf` RePEc (§2.1) + tests + re-run discover D1/D2.
2. Ajouter A1/A2 patterns pour Fed + ECB + BoE dans TOML.
3. Ajouter E2 patterns pour Fed + ECB + BoE + SNB + BoC.
4. Re-run `discover` ciblé sur les nouveaux types.

**Sprint 2 (2-3 jours)** — Extension
1. Étendre A1/A2 à 5 autres majors (BoJ, RBA, BdF, Bundesbank, Banca d'Italia).
2. Ajouter E2 + E3 pour 10 majors.
3. B1 transcripts Fed + ECB.

**Sprint 3 (3-5 jours)** — Couverture étendue
1. Étendre BIS speech mapping à ~130 banques (§2.6).
2. Adapters spécifiques pour les 5 banques absentes (§2.10).
3. A4 voting records pour BoE / Riksbank.

**Sprint 4** — Optimisations + production
1. Paralléliser `pipeline.run()`.
2. Endpoint API ou monitoring de re-run hebdomadaire.
3. Migration vers eigenmind (vérifier ingestion 17k+ PDFs).

---

## 6. Questions ouvertes

1. **Doit-on accepter du HTML pour des doc types autres qu'ECB A3** ? Aujourd'hui la politique html_to_pdf rend en PDF et préserve HTML. À confirmer pour les futurs sites.

2. **Langue** : actuellement on garde l'original (en majorité EN). Pour des banques non-EN (PBoC, Bundesbank, BdF, etc.) — leur version EN suffit-elle ou faut-il aussi la langue locale ? Inventaire §6 dit "non-English originals are kept as-is, no machine translation".

3. **Doublons** : un même speech peut être à la fois sur BIS (re-host) ET sur le site bank. Notre dédup `sha256` les attrape si le PDF est identique. Si la banque modifie légèrement, on aurait deux records. Probablement acceptable.

4. **OCR** : certains PDFs anciens sont scannés (sans couche texte). L'inventaire mentionne d'OCR localement (eigenmind le fait). À mesurer combien dans le corpus.

5. **Mise à jour incrémentale** : à quelle fréquence re-run ? Quotidien pour BIS sitemap ? Hebdomadaire pour les adapters par banque ?

---

## 7. Fichiers à toucher (récap)

| Fichier | Changement |
|---|---|
| `cb_corpus/sources/repec.py` | Fix `extract_pdf` pour `<input name="url">` |
| `cb_corpus/banks_sources.toml` | Ajouter sections A1/A2, E2, E3 par banque |
| `cb_corpus/banks.py` | (optionnel) ajouter `BIS_130` ou élargir registry |
| `cb_corpus/adapters/listing_crawler.py` | Possiblement améliorer pour pagination |
| `cb_corpus/cli.py` | Ajouter `--types` flag pré-existant suffit |
| `tests/test_framework.py` | Tests RePEc fix + nouveaux patterns |

---

## 8. Hors scope explicite

- OCR / extraction texte → géré par eigenmind en aval.
- Traduction → jamais (politique du projet).
- Long tail des ~120 banques non-BIS-63 (hors-BIS-130) → trop dispersé.
- Pages dynamiques nécessitant Playwright/Selenium → seulement si vraiment indispensable.

---

## 9. Ingestion dans eigenmind / Qdrant — stratégie de collections

### Le bon défaut : une seule collection `cb_corpus`

Pour qu'un prompt unique LLM puisse interroger **toute** la base, il faut une seule collection — Qdrant requête une collection à la fois, et le `/ask/` d'eigenmind aussi. Split en plusieurs collections force à choisir une "lens" avant la query, ce qui casse les questions transversales.

Commande :
```bash
eigenmind-ingest data/raw cb_corpus --device cpu
```

### Payload obligatoire par point

Chaque chunk Qdrant doit porter un payload riche pour le filtrage à la query :
```json
{
  "doc_id": "c7e8082e5b794451",
  "bank_code": "de",
  "doc_type": "C1",
  "year": 2024,
  "date": "2024-01-05",
  "language": "en",
  "provenance": "bis_index",
  "title": "Claudia Buch: Financial stability...",
  "url": "https://www.bis.org/review/r240105c.pdf"
}
```

À vérifier dans la doc eigenmind comment ces champs sont peuplés depuis le manifest. Si l'ingestion par défaut ne lit pas notre `manifest.jsonl`, un script wrapper sera nécessaire pour pousser le payload après ingestion (`qdrant_client.set_payload`).

### Le défi : déséquilibre 96 % C1 (speeches)

Avec une seule collection, les speeches dominent. Sans précaution, une query "que disent les CB sur l'inflation" retournera 8 discours là où un FSR + un MP report répondrait mieux. Mitigations :

1. **MMR retrieval** (Maximal Marginal Relevance) : top-K diversifié au lieu de top-K par similarité brute. Vérifier si eigenmind l'expose dans `/ask/`.
2. **Re-ranking par doc_type** : détecter le type de question (factuel → A/E/F ; opinion → C ; technique → D) et booster les chunks correspondants.
3. **Filtre payload à la query** : pour les questions ciblées ("que dit le FSR de la Fed sur X ?"), filtrer `doc_type IN ("E1","E2","E3")` ET `bank_code = "us"`. eigenmind doit exposer ça via l'API Qdrant ou un sélecteur UI.
4. **Hybrid retrieval BM25 + vectoriel** : eigenmind le mentionne dans son README. Les mots-clés discriminants ("minutes", "projection", "stability report") aident à retrouver le bon type.
5. **System prompt LLM** : indiquer "tu as accès à C1 speeches, A3 minutes, E reports. Privilégie la source officielle si plusieurs te répondent". Le LLM choisit.

### Quand split malgré tout

Justifications valables pour une 2e collection annexe (en plus de la principale) :
- Topic modeling fin sur un sous-corpus homogène (ex : `cb_research` pour les D uniquement → clusters plus propres).
- Knowledge graph navigation séparée (ex : KG des minutes Fed sans bruit de speeches).
- Near-duplicate detection sur un type spécifique (utile pour D où les WPs ont parfois des versions multiples).

Stratégie : **garder `cb_corpus` comme collection principale** pour /ask/, et créer des collections dérivées via re-ingestion d'un sous-ensemble (`data/by_purpose/research/` via symlinks par exemple).

### À éviter

- **Collection par banque (58 collections)** → ingérable côté UI, queries cross-banques impossibles.
- **Collection par année** → casse les comparaisons inter-temporelles.
- **Collection par doc_type seul (5 collections)** → moins utile que la collection unique avec filtre payload ; force à requêter 5× la même question.

### Plan d'ingestion suggéré

1. **Étape 1** — Ingérer tout en `cb_corpus` :
   ```bash
   eigenmind-ingest data/raw cb_corpus --device cpu
   ```
2. **Étape 2** — Vérifier que les payload metadata sont peuplés correctement (lire un point dans Qdrant et confirmer la présence de `bank_code`, `doc_type`, `year`).
3. **Étape 3** — Si l'ingestion par défaut ne peuple pas le payload depuis le manifest, écrire un script `scripts/enrich_qdrant_payload.py` qui :
   - lit `data/manifest.jsonl`,
   - pour chaque doc_id, retrouve les points Qdrant correspondants (filtre sur la source filename),
   - appelle `client.set_payload(collection_name="cb_corpus", payload={...}, points=[...])`.
4. **Étape 4** — Tester quelques queries via `/ask/` avec et sans filtre. Calibrer MMR / re-ranking si besoin.
5. **Étape 5** *(optionnel)* — Créer une collection annexe `cb_research` ou `cb_official` si l'analyse fine d'un sous-corpus le justifie.

### Questions ouvertes côté ingestion

- eigenmind chunke comment les PDFs longs (FSR 100+ pages) ? Configurable ?
- Le modèle d'embedding `multilingual-e5-base` (768-dim) gère bien le multilingue, mais comment performe-t-il sur le jargon de banques centrales ? À benchmark avec quelques queries dorées.
- Les 25 records sans HTML (HTML supprimé par l'ancienne politique) sont-ils ingérables sans souci ? Le PDF rendu suffit pour eigenmind, oui.
- Comment ingérer les futurs ajouts ? eigenmind a une fonction "smart resume" qui skip les fichiers déjà ingérés — confirmer qu'elle fonctionne sur ré-ingestion d'un dossier qui a grandi.
