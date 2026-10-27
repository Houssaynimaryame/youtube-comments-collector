# Collecteur de commentaires YouTube : Text Mining & Social Media Analysis

Mini-projet (Collecte des données des médias sociaux).

## 1. Protocole de collecte

| Élément | Choix |
|---|---|
| Question de recherche | Quelle opinion les utilisateurs expriment-ils à propos de l'intelligence artificielle ? |
| Population cible | Commentaires publics discutant de l'IA |
| Source | YouTube (YouTube Data API v3) |
| Unité d'analyse | Commentaire de premier niveau |
| Requête par défaut | `"artificial intelligence"\|"machine learning"` (`\|` = OR) |
| Période | Paramétrable (`--published-after`, `--published-before`) : filtre appliqué à la date de **publication des vidéos** |
| Langue | Paramétrable (`--relevance-lang` pour la recherche, `--filter-langs` pour le corpus) |
| Inclusion | Commentaires de premier niveau des vidéos trouvées, texte d'au moins 3 caractères |
| Exclusion | Textes vides/courts, doublons (même id ou même texte), hors langue, hors sujet (si mots-clés) |
| Métadonnées | id, vidéo, titre de la vidéo, texte, auteur pseudonymisé, dates, likes, nombre de réponses |

## 2. Installation

```bash
python -m venv .venv
.venv\Scripts\activate          # Windows  (Linux/Mac : source .venv/bin/activate)
pip install -r requirements.txt
```

Clé API : copier `config/.env.example` en `config/.env` et renseigner `YOUTUBE_API_KEY`.
Le fichier `.env` est exclu de Git (`.gitignore`) : **ne jamais le partager**.

## 3. Utilisation

```bash
python run.py --check-key                  # teste la clé (doit afficher 200)
python run.py --max-videos 3 --max-comments 200
python run.py --query "artificial intelligence|machine learning" \
              --published-after 2025-01-01 --published-before 2026-10-01 \
              --filter-langs en --max-videos 5 --max-comments 500
```

## 4. Structure

```
data/raw/        réponses brutes de l'API (jamais modifiées)
data/processed/  commentaires extraits (tableau plat)
data/analytical/ corpus final, dédupliqué et validé
data/metadata/   paramètres de collecte + rapport de validation
src/collector.py appels API, retry, pagination, extraction
src/validation.py doublons, valeurs manquantes, langues, dates
src/storage.py   sauvegarde
logs/            journal de chaque collecte
```

## 5. Gestion des limites

- `get_with_retry` : sur HTTP 429 / 5xx / erreur réseau, attend (`Retry-After` si présent,
  sinon backoff exponentiel 1, 2, 4... s) avec 5 tentatives maximum.
- Quota journalier épuisé : la collecte s'arrête proprement et conserve ce qui a été récupéré.
- Coût approximatif : `search.list` = 100 unités, `videos.list` et `commentThreads.list` = 1 unité
  (10 000 unités/jour par défaut). La consommation estimée figure dans les métadonnées.
- Pagination : `nextPageToken` jusqu'à la dernière page ou `--max-comments`.

## 6. Validation

Le rapport (`data/metadata/validation_report_*.json`) contient : taille, doublons, valeurs
manquantes, répartition des langues, période (date min / max), types de contenu, erreurs de
collecte, et la vérification de la formule
`N_final = N_collecté - N_doublons - N_hors_sujet - N_invalides (- N_hors_langue)`.

## 7. Limites et biais

- Le tri `order=time` prend les commentaires les plus récents de chaque vidéo : le corpus
  n'est pas un échantillon représentatif de tous les commentaires sur l'IA.
- La recherche YouTube retourne des vidéos "pertinentes" selon l'algorithme de YouTube
  (biais de popularité/recommandation).
- Seuls les commentaires de premier niveau sont collectés (pas les réponses).
- La détection de langue est peu fiable sur les textes très courts.
- Les commentaires reflètent les utilisateurs de YouTube, pas la population générale.

## 8. Éthique et cadre légal

- Seules des données publiques sont collectées via l'API officielle, dans le respect de ses
  conditions d'utilisation.
- Minimisation : seuls les champs nécessaires sont conservés ; le nom des auteurs n'est pas
  stocké dans le corpus (identifiant pseudonymisé par hachage). Les données **brutes** locales
  contiennent toutefois les informations renvoyées par l'API : elles ne doivent pas être partagées.
- Cadre marocain : loi 09-08 et CNDP (voir chapitre, section éthique).
- Ne pas republier les commentaires de façon à permettre de ré-identifier leurs auteurs.

## 9. Résultats de la collecte (27/10/2026)

| Paramètre | Valeur |
|---|---|
| Requête | `"artificial intelligence"\|"machine learning"\|"ChatGPT"` |
| Vidéos retenues | 8 (sur 40 candidates ; seuil : au moins 100 commentaires) |
| Période des vidéos | 01/01/2025 - 27/10/2026 |
| Tri des commentaires | pertinence (`relevance`), 300 commentaires maximum par vidéo |
| Langues conservées | en, fr |
| Quota API consommé | 119 unités (sur 10 000/jour) |

```
1 675 collectés - 11 invalides - 29 doublons - 181 hors langue (- 0 hors sujet) = 1 454 commentaires
```

- Période des commentaires : 17/10/2025 - 27/10/2026.
- Langues : en 1 435, fr 11, indéterminée 8 : le corpus est **quasi exclusivement anglophone**.
- Une première collecte exploratoire (3 vidéos) était déséquilibrée : 173 commentaires sur 203 venaient d'une seule vidéo,
  sur une semaine. Un seuil minimal de commentaires par vidéo et un tri par pertinence ont été ajoutés.
- Les métadonnées complètes sont dans `data/metadata/`. Le corpus lui-même n'est pas publié
  (textes d'utilisateurs ; voir section 8).
- Les résultats dépendent de l'état de YouTube au moment de la collecte : une nouvelle exécution donnera un corpus différent.

