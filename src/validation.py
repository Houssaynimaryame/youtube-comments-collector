"""Validation du corpus : structure, doublons, valeurs manquantes, langues, dates.

Formule du cours (slide 112) :
    N_final = N_collecté - N_doublons - N_hors_sujet - N_invalides
(ici, les textes hors langue demandée sont comptés à part).
"""
import re

import pandas as pd

REQUIRED_COLUMNS = [
    "comment_id", "video_id", "text", "author_hash",
    "published_at", "like_count", "content_type",
]


def detect_language(text):
    """Langue détectée (code ISO) ou 'unknown' si impossible."""
    try:
        from langdetect import DetectorFactory, detect
        DetectorFactory.seed = 0  # résultats reproductibles
        return detect(text)
    except Exception:
        return "unknown"


def _normalize(text):
    return re.sub(r"\s+", " ", str(text).strip().lower())


def validate(df, collection_stats=None, keywords=None, allowed_langs=None, min_chars=3):
    """Nettoie et valide le corpus. Renvoie (corpus_final, rapport)."""
    report = {"n_collecte": int(len(df))}

    # 1. Structure
    missing_cols = [c for c in REQUIRED_COLUMNS if c not in df.columns]
    report["colonnes_manquantes"] = missing_cols
    if missing_cols:
        raise ValueError(f"Colonnes manquantes : {missing_cols}")

    df = df.copy()

    # 2. Valeurs manquantes (avant nettoyage)
    report["valeurs_manquantes"] = {
        c: int(df[c].isna().sum()) for c in REQUIRED_COLUMNS
    }

    # 3. Textes invalides (vides ou trop courts)
    text = df["text"].fillna("").astype(str).str.strip()
    invalid_mask = text.str.len() < min_chars
    report["n_invalides"] = int(invalid_mask.sum())
    df = df[~invalid_mask].copy()
    df["text"] = df["text"].astype(str).str.strip()

    # 4. Doublons (même identifiant, puis même texte normalisé)
    n_before = len(df)
    df = df.drop_duplicates(subset="comment_id", keep="first")
    n_dup_id = n_before - len(df)

    n_before = len(df)
    df["_norm"] = df["text"].map(_normalize)
    df = df.drop_duplicates(subset="_norm", keep="first")
    n_dup_text = n_before - len(df)
    report["n_doublons_id"] = int(n_dup_id)
    report["n_doublons_texte"] = int(n_dup_text)
    report["n_doublons"] = int(n_dup_id + n_dup_text)

    # 5. Langues
    df["lang"] = df["text"].map(detect_language)
    report["n_hors_langue"] = 0
    if allowed_langs:
        mask = df["lang"].isin(allowed_langs) | (df["lang"] == "unknown")
        report["n_hors_langue"] = int((~mask).sum())
        df = df[mask]

    # 6. Pertinence thématique (optionnelle)
    report["n_hors_sujet"] = 0
    if keywords:
        pattern = "|".join(re.escape(k.lower()) for k in keywords)
        mask = df["text"].str.lower().str.contains(pattern, regex=True)
        report["n_hors_sujet"] = int((~mask).sum())
        df = df[mask]

    df = df.drop(columns=["_norm"]).reset_index(drop=True)

    # 7. Dates
    dates = pd.to_datetime(df["published_at"], errors="coerce", utc=True)
    report["dates_invalides"] = int(dates.isna().sum())
    report["date_min"] = str(dates.min()) if len(df) else None
    report["date_max"] = str(dates.max()) if len(df) else None

    # 8. Répartitions
    report["repartition_langues"] = {
        k: int(v) for k, v in df["lang"].value_counts().items()
    }
    report["repartition_types"] = {
        k: int(v) for k, v in df["content_type"].value_counts().items()
    }
    report["commentaires_par_video"] = {
        k: int(v) for k, v in df["video_id"].value_counts().items()
    }
    report["n_videos"] = int(df["video_id"].nunique())

    # 9. Taille finale + vérification de la formule du cours
    report["n_final"] = int(len(df))
    expected = (
        report["n_collecte"] - report["n_doublons"] - report["n_hors_sujet"]
        - report["n_invalides"] - report["n_hors_langue"]
    )
    report["formule_verifiee"] = bool(expected == report["n_final"])

    # 10. Erreurs de collecte
    if collection_stats:
        report["erreurs_collecte"] = {
            "requetes": collection_stats.get("requests", 0),
            "reessais": collection_stats.get("retries", 0),
            "requetes_echouees": collection_stats.get("failed_requests", 0),
            "videos_commentaires_desactives": collection_stats.get(
                "videos_comments_disabled", 0
            ),
            "videos_en_echec": collection_stats.get("videos_failed", 0),
        }

    return df, report


def print_report(report):
    print("\n===== RAPPORT DE VALIDATION =====")
    print(f"Collectés          : {report['n_collecte']}")
    print(f"- Invalides/vides  : {report['n_invalides']}")
    print(f"- Doublons         : {report['n_doublons']} "
          f"(id: {report['n_doublons_id']}, texte: {report['n_doublons_texte']})")
    print(f"- Hors langue      : {report['n_hors_langue']}")
    print(f"- Hors sujet       : {report['n_hors_sujet']}")
    print(f"= Corpus final     : {report['n_final']} "
          f"(formule vérifiée : {report['formule_verifiee']})")
    print(f"Période            : {report['date_min']} -> {report['date_max']}")
    print(f"Langues            : {report['repartition_langues']}")
    print(f"Vidéos             : {report['n_videos']}")
    print("=================================\n")
