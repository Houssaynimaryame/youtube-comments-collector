"""Pipeline complet : collecte -> validation -> sauvegarde -> métadonnées.

Exemples :
    python run.py --check-key
    python run.py --max-videos 3 --max-comments 200
    python run.py --query "artificial intelligence|machine learning" \
                  --published-after 2025-01-01 --published-before 2026-10-01 \
                  --filter-langs en --max-videos 5 --max-comments 500
"""
import argparse
import logging
import platform
import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import requests

from src import collector, storage, validation

BASE_DIR = Path(__file__).resolve().parent
ENV_PATH = BASE_DIR / "config" / ".env"

QUESTION = "Quelle opinion les utilisateurs expriment-ils à propos de l'intelligence artificielle ?"


def parse_args(argv=None):
    p = argparse.ArgumentParser(description="Collecteur de commentaires YouTube")
    p.add_argument("--check-key", action="store_true",
                   help="Teste uniquement la clé API puis quitte")
    # Opérateur OR de YouTube : le caractère |
    p.add_argument("--query", default='"artificial intelligence"|"machine learning"',
                   help="Requête de recherche YouTube (| = OR, - = exclusion)")
    p.add_argument("--max-videos", type=int, default=5)
    p.add_argument("--max-comments", type=int, default=300,
                   help="Commentaires max par vidéo")
    p.add_argument("--published-after", default=None, help="YYYY-MM-DD")
    p.add_argument("--published-before", default=None, help="YYYY-MM-DD")
    p.add_argument("--relevance-lang", default=None,
                   help="Langue de pertinence pour la recherche (ex : en)")
    p.add_argument("--filter-langs", default=None,
                   help="Langues à conserver dans le corpus, ex : en,fr")
    p.add_argument("--keywords", default=None,
                   help="Mots-clés de pertinence (séparés par des virgules) pour filtrer les commentaires")
    p.add_argument("--min-chars", type=int, default=3)
    p.add_argument("--min-video-comments", type=int, default=0,
                   help="Ne garder que les vidéos ayant au moins N commentaires "
                        "(la recherche récupère alors plus de candidats)")
    p.add_argument("--comment-order", choices=["time", "relevance"], default="time",
                   help="time = commentaires les plus récents, relevance = les plus pertinents")
    return p.parse_args(argv)


def to_rfc3339(date_str, end_of_day=False):
    if not date_str:
        return None
    suffix = "T23:59:59Z" if end_of_day else "T00:00:00Z"
    return f"{date_str}{suffix}"


def check_key(api_key):
    r = requests.get(
        f"{collector.BASE_URL}/videos",
        params={"part": "snippet", "id": "dQw4w9WgXcQ", "key": api_key},
        timeout=30,
    )
    print("Code HTTP :", r.status_code)
    if r.status_code == 200:
        print("Clé valide. Titre de test :", r.json()["items"][0]["snippet"]["title"])
        return 0
    print(r.text.replace(api_key, "***"))
    if r.status_code == 400:
        print("-> 400 : la clé est probablement mal copiée. Recopiez-la depuis "
              "console.cloud.google.com > API et services > Identifiants.")
    elif r.status_code == 403:
        print("-> 403 : API non activée, restriction de clé incorrecte ou quota épuisé.")
    return 1


def setup_logging(log_path):
    logging.basicConfig(
        force=True,  # nécessaire dans Jupyter (handlers déjà configurés)
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=[logging.FileHandler(log_path, encoding="utf-8"),
                  logging.StreamHandler(sys.stdout)],
    )


def main(argv=None):
    args = parse_args(argv)
    api_key = collector.load_api_key(ENV_PATH)

    if args.check_key:
        return check_key(api_key)

    storage.ensure_dirs(BASE_DIR)
    ts = storage.timestamp()
    setup_logging(BASE_DIR / "logs" / f"collecte_{ts}.log")
    log = logging.getLogger("run")
    started = datetime.now(timezone.utc)

    stats = collector.new_stats()
    interrupted = None

    # --- 1. Recherche des vidéos ---
    log.info("Recherche : %s", args.query)
    n_search = args.max_videos
    if args.min_video_comments > 0:
        n_search = min(50, args.max_videos * 5)  # candidats (1 seule page de recherche)
    try:
        video_ids = collector.search_videos(
            api_key, args.query, stats,
            max_videos=n_search,
            published_after=to_rfc3339(args.published_after),
            published_before=to_rfc3339(args.published_before, end_of_day=True),
            relevance_language=args.relevance_lang,
        )
    except (collector.QuotaExceeded, collector.APIError) as exc:
        log.error("Recherche impossible : %s", exc)
        return 1
    log.info("%d vidéos trouvées", len(video_ids))
    if not video_ids:
        log.error("Aucune vidéo trouvée : modifier la requête ou la période.")
        return 1

    # --- 2. Détails des vidéos ---
    try:
        video_items = collector.get_video_details(api_key, video_ids, stats)
    except (collector.QuotaExceeded, collector.APIError) as exc:
        log.error("Détails des vidéos impossibles : %s", exc)
        return 1
    video_meta = collector.extract_video_meta(video_items)

    # --- 2bis. Sélection des vidéos ayant assez de commentaires ---
    if args.min_video_comments > 0:
        selected = [v for v in video_ids
                    if int(video_meta.get(v, {}).get("comment_count") or 0)
                    >= args.min_video_comments]
        log.info("%d/%d vidéos ont au moins %d commentaires",
                 len(selected), len(video_ids), args.min_video_comments)
        video_ids = selected[:args.max_videos]
        if not video_ids:
            log.error("Aucune vidéo avec assez de commentaires : baisser --min-video-comments.")
            return 1

    # --- 3. Commentaires (avec pagination) ---
    raw_comments = []
    for i, vid in enumerate(video_ids, 1):
        log.info("[%d/%d] Commentaires de %s", i, len(video_ids), vid)
        try:
            items = collector.get_comments(
                api_key, vid, stats, args.max_comments, order=args.comment_order)
            raw_comments.extend(items)
            log.info("   -> %d commentaires", len(items))
        except collector.CommentsDisabled:
            stats["videos_comments_disabled"] += 1
            log.warning("   Commentaires désactivés, vidéo ignorée.")
        except collector.QuotaExceeded as exc:
            interrupted = str(exc)
            log.error("%s Collecte interrompue, on conserve l'existant.", exc)
            break
        except collector.APIError as exc:
            stats["videos_failed"] += 1
            log.error("   Échec sur cette vidéo : %s", exc)

    if not raw_comments:
        log.error("Aucun commentaire collecté.")
        return 1

    # --- 4. Données brutes (jamais modifiées) ---
    raw_path = storage.save_json(
        {"videos": video_items, "comment_threads": raw_comments},
        BASE_DIR / "data" / "raw" / f"youtube_raw_{ts}.json",
    )

    # --- 5. Extraction (texte + métadonnées) ---
    records = [collector.extract_comment(it, video_meta) for it in raw_comments]
    df_processed = pd.DataFrame(records)
    processed_path = storage.save_csv(
        df_processed, BASE_DIR / "data" / "processed" / f"comments_processed_{ts}.csv"
    )

    # --- 6. Validation, doublons, filtrage ---
    langs = [x.strip() for x in args.filter_langs.split(",")] if args.filter_langs else None
    kws = [x.strip() for x in args.keywords.split(",")] if args.keywords else None
    corpus, report = validation.validate(
        df_processed, stats, keywords=kws, allowed_langs=langs, min_chars=args.min_chars
    )
    validation.print_report(report)

    # --- 7. Corpus analytique + métadonnées ---
    analytical_path = storage.save_csv(
        corpus, BASE_DIR / "data" / "analytical" / f"corpus_{ts}.csv"
    )
    storage.save_json(report, BASE_DIR / "data" / "metadata" / f"validation_report_{ts}.json")

    finished = datetime.now(timezone.utc)
    metadata = {
        "question_de_recherche": QUESTION,
        "source": "YouTube Data API v3",
        "unite_d_analyse": "commentaire de premier niveau",
        "date_collecte_debut": started.isoformat(),
        "date_collecte_fin": finished.isoformat(),
        "parametres": {
            "requete": args.query,
            "max_videos": args.max_videos,
            "max_commentaires_par_video": args.max_comments,
            "publie_apres": args.published_after,
            "publie_avant": args.published_before,
            "langue_pertinence_recherche": args.relevance_lang,
            "langues_conservees": langs,
            "mots_cles_pertinence": kws,
            "longueur_min_texte": args.min_chars,
            "tri_commentaires": args.comment_order,
            "min_commentaires_par_video": args.min_video_comments,
        },
        "criteres_inclusion": [
            "Commentaires de premier niveau de vidéos retournées par la recherche",
            "Texte d'au moins %d caractères" % args.min_chars,
        ] + (["Vidéo avec au moins %d commentaires" % args.min_video_comments]
             if args.min_video_comments else []) + (["Langue dans : %s" % ",".join(langs)] if langs else [])
          + (["Contient un des mots-clés : %s" % ",".join(kws)] if kws else []),
        "criteres_exclusion": [
            "Textes vides ou trop courts",
            "Doublons (même comment_id ou même texte normalisé)",
        ] + (["Langue détectée hors liste"] if langs else [])
          + (["Hors sujet (aucun mot-clé)"] if kws else []),
        "transformations": [
            "Extraction des champs : id, vidéo, texte, date, likes, réponses",
            "Pseudonymisation de l'auteur (SHA-256 tronqué à 12 caractères)",
            "Suppression des textes invalides et des doublons",
            "Détection de langue (langdetect)",
        ],
        "collecte_interrompue": interrupted,
        "statistiques_collecte": stats,
        "validation": report,
        "fichiers": {
            "brut": str(raw_path.relative_to(BASE_DIR)),
            "prepare": str(processed_path.relative_to(BASE_DIR)),
            "analytique": str(analytical_path.relative_to(BASE_DIR)),
        },
        "environnement": {"python": platform.python_version(),
                          "pandas": pd.__version__},
    }
    meta_path = storage.save_json(
        metadata, BASE_DIR / "data" / "metadata" / f"collection_info_{ts}.json"
    )
    log.info("Corpus final : %s (%d commentaires)", analytical_path, report["n_final"])
    log.info("Métadonnées  : %s", meta_path)
    log.info("Quota estimé consommé : %d unités", stats["quota_units"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
