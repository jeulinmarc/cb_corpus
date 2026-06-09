# Corpus → RAG : contrat de données & handoff

> ℹ️ **L'ingestion elle-même est gérée par un repo séparé.** Ce document est le **contrat de
> données** : ce que `cb_corpus` expose (schéma, chemins, filtres, citation, pièges) pour que
> n'importe quel ingéreur puisse le consommer. Les extraits de code sont une **référence**, à
> adapter à la stack du repo d'ingestion — pas une implémentation imposée.

Tout part de `data/raw/` (fichiers) + `data/manifest.jsonl` (l'index). L'objectif côté RAG :
**filtres de métadonnées** + **citations de source officielle**.

> Inventaire des données (par pays / type) : `CORPUS.md` (généré). Couverture ~99 % vs les catalogues officiels.

---

## 1. Le manifest est ton index

`data/manifest.jsonl` = 1 ligne JSON par document. **N'itère pas le disque, itère le manifest** :
il porte toutes les métadonnées et pointe vers le fichier local.

> 🗄️ **Le store requêtable est ta responsabilité (côté RAG).** `cb_corpus` produit un **handoff**
> (JSONL dédupliqué + fichiers raw) ; c'est à ce repo de l'ingérer dans son **store indexé/vectoriel**
> (SQLite, pgvector, etc.). Le builder reste volontairement en JSONL (le store requêtable est côté repo RAG).
> `doc_id` est **date-indépendant** (hash de bank+type+url) → stable comme `id` de document.

Champs utiles à l'ingestion :

| Champ | Usage RAG |
|---|---|
| `local_path` | **chemin du fichier à lire** (PDF, ou HTML si le rendu a échoué) |
| `bank_code` | **filtre** + métadonnée (mapper vers nom complet via `cb_corpus.banks`) |
| `doc_type` | **filtre** + métadonnée (mapper vers libellé lisible, cf. §5) |
| `year`, `date` | **filtre temporel** / facette |
| `title` | titre du chunk / affichage |
| `pdf_url` | **citation** (source officielle) |
| `language` | filtre (tout `en` aujourd'hui) |
| `doc_id` | clé primaire stable (idéale comme `id` de document dans le store) |
| `sha256` | détection de changement / idempotence d'ingestion |

---

## 2. Pipeline d'ingestion minimal

```python
import json
from pathlib import Path
import fitz  # PyMuPDF — rapide et robuste pour l'extraction texte PDF

from cb_corpus.banks import get_bank
from cb_corpus.taxonomy import by_code

ROOT = Path("/Users/marc/Desktop/All CODING/GENERALI/cb_corpus")
MANIFEST = ROOT / "data" / "manifest.jsonl"

def iter_docs():
    for line in MANIFEST.open():
        d = json.loads(line)
        # n'ingère que les PDF réellement présents
        lp = d.get("local_path")
        if not lp or not lp.endswith(".pdf"):
            continue
        p = ROOT / lp
        if p.exists():
            yield d, p

def extract_text(pdf_path: Path) -> str:
    with fitz.open(pdf_path) as doc:
        return "\n".join(page.get_text("text") for page in doc)

def doc_metadata(d: dict) -> dict:
    """Métadonnées propres, prêtes pour le filtrage + la citation."""
    return {
        "doc_id":   d["doc_id"],
        "bank_code": d["bank_code"],
        "bank_name": get_bank(d["bank_code"]).name,
        "doc_type":  d["doc_type"],
        "doc_type_label": by_code(d["doc_type"]).label,  # ex. "Speech"
        "year":   d.get("year"),
        "date":   d.get("date"),
        "title":  d.get("title", ""),
        "source_url": d.get("pdf_url"),   # <- la citation officielle
        "language":   d.get("language", "en"),
    }
```

---

## 3. Chunking

Recommandations pour ce corpus (discours + rapports, souvent longs) :

- **Taille** : ~800–1 200 tokens par chunk, **overlap** ~100–150 tokens.
- **Découpe sémantique** d'abord (paragraphes / sauts de ligne), puis regroupe jusqu'à la
  taille cible — évite de couper en plein milieu de phrase.
- **Reporte les métadonnées du document sur chaque chunk** (même `doc_id`, `bank_code`,
  `doc_type`, `year`, `source_url`, `title`) + un `chunk_index`. C'est ce qui permet le
  filtrage et la citation au niveau du chunk.

```python
def chunk_text(text, target=1000, overlap=120):
    paras = [p.strip() for p in text.split("\n") if p.strip()]
    chunks, cur = [], ""
    for p in paras:
        if len(cur) + len(p) > target * 4:   # ~4 chars/token, approximation
            chunks.append(cur)
            cur = cur[-overlap * 4:] + "\n" + p
        else:
            cur = (cur + "\n" + p) if cur else p
    if cur:
        chunks.append(cur)
    return chunks

def build_records():
    for d, path in iter_docs():
        meta = doc_metadata(d)
        try:
            text = extract_text(path)
        except Exception:
            continue                          # PDF illisible -> on saute
        for i, ch in enumerate(chunk_text(text)):
            yield {**meta, "chunk_index": i, "text": ch,
                   "id": f"{meta['doc_id']}:{i}"}
```

Branche `build_records()` sur ton vector store (Chroma, Qdrant, pgvector, FAISS…), en mettant
`bank_code` / `doc_type` / `year` comme **métadonnées filtrables**.

---

## 4. Filtrer au retrieval

Les filtres de métadonnées rendent les réponses ciblées et citables :

| Question | Filtre |
|---|---|
| « Que dit la BCE sur l'inflation en 2023 ? » | `bank_code = "ecb" AND year = 2023` |
| « Minutes de la Fed » | `bank_code = "us" AND doc_type = "A3"` |
| « Discours des banques de la zone euro depuis 2020 » | `bank_code IN (…) AND doc_type = "C1" AND year >= 2020` |

---

## 5. Libellés de types (pour l'affichage)

`doc_type` est un code ; mappe-le pour l'utilisateur (`cb_corpus.taxonomy.by_code(code).label`) :

| Code | Libellé |
|---|---|
| A1 | Rate-decision press release |
| A2 | Monetary policy statement |
| A3 | Meeting minutes / accounts |
| B1 | Press-conference transcript / Q&A |
| C1 | Speech |
| C2 | Interview / op-ed / testimony |
| D1 / D2 | Working paper / occasional paper |
| D3 | Economic letter / research blog |
| E1 | Monetary policy / inflation report |
| E2 | Financial Stability Review |
| E3 | Annual / convergence report |
| E4 | Economic / quarterly bulletin |
| F1 | Staff economic projections |
| G2 | Statistical release / survey |

---

## 6. Citation

À chaque réponse, renvoie la **source officielle** : `title` + `bank_name` + `date` + `source_url`
(`pdf_url`). Exemple de rendu :

> *« … »* — Bank of England, *Andrew Bailey: Monetary policy and the outlook*, 2023-05-18.
> Source : https://www.bis.org/review/r230518a.pdf

---

## 7. Caveats à connaître pour pondérer le RAG

- **Composition** : le corpus est dominé par les **discours (C1)**. C'est une base « parole de
  banquier central » ; les décisions chiffrées (A1/A2) et la recherche (D1/D2) sont présentes. Pondère le retrieval selon le cas d'usage.
- **Langue** : 100 % anglais. Les discours de banques non-anglophones sont les **versions EN
  officielles**, pas les originaux.
- **À exclure de l'ingestion** : fichiers `.html` orphelins dans `data/raw/` (pages non
  converties), `.DS_Store`. Le filtre `local_path.endswith(".pdf")` du §2 s'en charge.
- **Working papers (D1/D2)** : la date vient des métadonnées IDEAS ; un petit nombre peut être
  sans date (rangé sous `year` nul) — filtrer/gérer si besoin.
- **Idempotence** : ré-ingère en te basant sur `sha256` (inchangé = déjà indexé) pour éviter
  de tout recalculer à chaque mise à jour du corpus.

---

## 8. Dépendances suggérées

```bash
pip install pymupdf          # extraction texte PDF (fitz)
# + ton vector store / framework : chromadb | qdrant-client | langchain | llama-index …
```
