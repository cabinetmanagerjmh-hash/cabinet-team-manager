# -*- coding: utf-8 -*-
"""Notifications in-app rédigées par ASTRO.

ASTRO ne décide de rien : il reformule un changement de statut déjà enregistré
en base. Le texte est contrôlé (titre exact + statut exact) ; au moindre doute,
l'appelant garde son template. Budget et timeout courts : une notification ne
doit jamais bloquer une synchro ni coûter une rafale d'appels LLM.
"""
from __future__ import annotations

import json
import logging
from typing import Optional

logger = logging.getLogger(__name__)

PREFIXE = 'ASTRO · '
LONGUEUR_MAX = 170
TIMEOUT_SECONDES = 5
MAX_PAR_CONTEXTE = 3

_STATUTS = {'a_faire': 'à faire', 'en_cours': 'en cours', 'terminee': 'terminée'}
_ORIGINES = {'auto_pennylane': 'la synchro Pennylane', 'auto_checklist': 'la checklist'}


def _budget_disponible() -> bool:
    try:
        from flask import g
        if not hasattr(g, '_astro_notifications_restantes'):
            g._astro_notifications_restantes = MAX_PAR_CONTEXTE
        if g._astro_notifications_restantes <= 0:
            return False
        g._astro_notifications_restantes -= 1
        return True
    except Exception:
        return True


def _appel_rapide(messages):
    """Un seul provider, timeout court : pas de cascade de secours ici."""
    from .integrations.llm_provider import get_llm_manager
    manager = get_llm_manager()
    for prov in (manager.primary, manager.fallback):
        if prov and prov.is_configured():
            try:
                return prov.chat(messages, timeout=TIMEOUT_SECONDES)
            except Exception as exc:
                logger.warning('ASTRO notification: %s', type(exc).__name__)
                return None
    return None


def _texte_valide(brut, tache, nouveau_statut) -> Optional[str]:
    texte = ' '.join((brut or '').split()).strip()
    if not texte or len(texte) > LONGUEUR_MAX:
        return None
    if any(c in texte for c in '<>{}`'):
        return None
    titre = ' '.join((tache.titre or '').split())
    libelle = _STATUTS.get(nouveau_statut, nouveau_statut.replace('_', ' '))
    if titre.lower() not in texte.lower() or libelle.lower() not in texte.lower():
        return None
    return texte


def rediger_notification_statut(tache, nouveau_statut, source, acteur=None,
                                ancien_statut=None, force=False) -> Optional[str]:
    """Retourne « ASTRO · ... », ou None pour laisser l'appelant utiliser son template.

    force=True (tests) ignore le garde-fou TESTING.
    """
    from flask import current_app
    try:
        testing = bool(current_app.config.get('TESTING'))
    except RuntimeError:
        testing = False
    if testing and not force:
        return None
    if not _budget_disponible():
        return None

    faits = {
        'tache': tache.titre,
        'dossier': tache.dossier.numero_dossier if tache.dossier else None,
        'statut_precedent': _STATUTS.get(ancien_statut, ancien_statut),
        'nouveau_statut': _STATUTS.get(nouveau_statut, nouveau_statut),
        'origine': _ORIGINES.get(source, 'un changement automatique'),
        'acteur': f'{acteur.prenom} {acteur.nom}' if acteur else None,
        'echeance': tache.date_echeance.strftime('%d/%m/%Y') if tache.date_echeance else None,
    }
    messages = [
        {'role': 'system', 'content': (
            "Tu es ASTRO, l'assistant de JMH Orbit. Rédige UNE notification in-app "
            "en français, factuelle et courte (max 160 caractères), sans emoji, sans "
            "markdown, sans guillemets autour du message. Reprends exactement le titre "
            "de la tâche et le nouveau statut fournis. N'invente aucun fait, aucun "
            "chiffre, aucun nom. Ne donne pas de conseil.")},
        {'role': 'user', 'content': json.dumps(faits, ensure_ascii=False)},
    ]
    try:
        brut = _appel_rapide(messages)
    except Exception as exc:
        logger.warning('ASTRO notification indisponible: %s', type(exc).__name__)
        return None
    texte = _texte_valide(brut, tache, nouveau_statut)
    return (PREFIXE + texte) if texte else None