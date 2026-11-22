# -*- coding: utf-8 -*-
"""Liaison bidirectionnelle : cases Checklist (déclarations TVA/IS/CFE) <->
taches deadline correspondantes (Dépôt TVA, Acompte IS, CFE...).

Sens A/B  — case -> tache : `appliquer_case_a_taches()` appelle quand une case
             devient declare (manuel via /checklist/toggle OU via synchro Pennylane)
             => la tache deadline passe en_cours, puis terminee si la declaration
             est acceptee ou la case soldee. Case videe => tache a_faire.
Sens C    — tache -> case : `appliquer_tache_a_case()` appelee quand un utilisateur
             change le statut d'une tache deadline => en_cours/terminee coche la case,
             a_faire la vide.

Mapping periode (aligne sur la logique « seed » de la checklist, routes.py) :
  Dépôt TVA mensuel/trimestriel : periode declaree = mois precedant l'echeance
  Acompte TVA annuel (juillet|decembre) : (annee echeance, mois echeance, acompte)
  Declaration TVA annuelle : (annee echeance, 5, declaration)
  Acompte IS : (annee echeance, mois echeance, acompte)
  Declaration IS definitive : (annee echeance, 5, declaration)
  CFE : (annee echeance, 12, depot)
"""
from datetime import datetime

from . import db
from .models import Tache, ChecklistEntry, Notification, User

_KINDE = ('tva_mensuel', 'tva_trimestriel', 'tva_ca12', 'is', 'cfe')


def _periode_depuis_deadline(dt, freq):
    """Alignee sur routes.checklist : mensuel 24/08 -> juillet (07)."""
    y, m = dt.year, dt.month - 1
    if m == 0:
        y, m = y - 1, 12
    if freq == 'trimestriel':
        m = ((m - 1) // 3) * 3 + 1
    return y, m


def case_pour_tache(tache):
    """Retourne (taxe, annee, mois, kind, paye) si la tache est une echeance
    fiscale liee a la checklist, sinon None. Ignore les Preparations."""
    titre = (tache.titre or '').lower()
    if 'préparation' in titre or 'preparation' in titre:
        return None
    if not tache.date_echeance:
        return None
    e = tache.date_echeance
    if 'dépôt tva mensuel' in titre or 'depot tva mensuel' in titre:
        y, mo = _periode_depuis_deadline(e, 'mensuel')
        return ('tva_mensuel', y, mo, 'depot', False)
    if 'dépôt tva trimestriel' in titre or 'depot tva trimestriel' in titre:
        y, mo = _periode_depuis_deadline(e, 'trimestriel')
        return ('tva_trimestriel', y, mo, 'depot', False)
    if 'acompte tva annuel' in titre:
        return ('tva_ca12', e.year, e.month, 'acompte', True)
    if 'déclaration tva annuelle' in titre or 'declaration tva annuelle' in titre:
        return ('tva_ca12', e.year, e.month, 'declaration', False)
    if 'acompte is' in titre:
        return ('is', e.year, e.month, 'acompte', True)
    if 'déclaration is' in titre or 'declaration is' in titre:
        return ('is', e.year, e.month, 'declaration', False)
    if 'cfe' in titre:
        return ('cfe', e.year, e.month, 'depot', False)
    return None


def _taches_pour_case(dossier_id, taxe, annee, mois, kind):
    """Les taches deadline du dossier qui correspondent a la case donnee."""
    cands = Tache.query.filter(Tache.dossier_id == dossier_id).all()
    out = []
    for t in cands:
        m = case_pour_tache(t)
        if m and m[:4] == (taxe, annee, mois, kind):
            out.append(t)
    return out


_LIBELLE_SOURCE_AUTO = {
    'auto_checklist': 'la Checklist',
    'auto_pennylane': 'la synchro Pennylane',
}


def _destinataires_changement_auto(tache, acteur):
    """Ids des personnes a informer d'un changement de statut automatique.

    Ordre de priorite (pour l'email) : createur de la tache (souvent le manager
    assigneur), puis l'assigne, puis le manager de l'equipe du dossier.
    Doublons supprimes ; l'auteur du changement (acteur) n'est jamais notifie."""
    ids = []

    def _ajouter(uid):
        if uid and uid not in ids:
            ids.append(uid)

    _ajouter(tache.cree_par)
    _ajouter(tache.assigne_a)
    equipe = tache.dossier.equipe if tache.dossier else None
    if equipe and equipe.manager:
        _ajouter(equipe.manager.id)
    if acteur is not None and acteur.id in ids:
        ids.remove(acteur.id)
    return ids


def notifier_changement_auto(tache, source, nouveau_statut, acteur=None, email=True,
                             ancien_statut=None):
    """Previent les personnes concernees qu'une tache a change de statut
    automatiquement (case Checklist ou synchro Pennylane).

    Notification in-app pour le createur de la tache, l'assigne et le manager
    de l'equipe du dossier (doublons et acteur exclus) : le changement de
    statut declenche par la synchro doit etre visible par ceux qui suivent la
    tache, pas seulement par le createur. Le texte est redige par ASTRO quand
    c'est possible, sinon on garde un message factuel de repli.
    L'email (si email=True) part uniquement au destinataire principal.
    Ne fait pas de commit : l'appelant commit. Retourne True si au moins une
    notification in-app a ete ajoutee."""
    from . import app
    dest_ids = _destinataires_changement_auto(tache, acteur)
    if not dest_ids:
        return False
    origine = _LIBELLE_SOURCE_AUTO.get(source, 'un changement automatique')
    statut_txt = nouveau_statut.replace('_', ' ')
    qui = f" (par {acteur.prenom} {acteur.nom})" if acteur else ''
    message = f"Statut automatique : \"{tache.titre}\" passe a \"{statut_txt}\" via {origine}{qui}"
    try:
        from .astro_notifications import rediger_notification_statut
        message = rediger_notification_statut(
            tache, nouveau_statut, source, acteur, ancien_statut=ancien_statut) or message
    except Exception as exc:
        app.logger.warning(f"ASTRO notification: {exc}")
    # Type dedie : la page Notifications isole la synchro Pennylane du reste.
    type_notif = 'pennylane' if source == 'auto_pennylane' else 'systeme'
    for uid in dest_ids:
        db.session.add(Notification(user_id=uid, tache_id=tache.id, message=message,
                                    type_notification=type_notif))
    if email:
        try:
            dest = User.query.get(dest_ids[0])
            if dest and dest.email:
                from app.integrations.brevo import send_email_via_brevo_api
                corps = f"Bonjour {dest.prenom},\n\n{message}.\n\nCabinet JMH"
                envoye = send_email_via_brevo_api(
                    to_email=dest.email,
                    subject=f"Changement de statut automatique : {tache.titre}",
                    body=corps)
                app.logger.info(f"Email statut auto to {dest.email}: {'OK' if envoye else 'ECHEC'}")
        except Exception as e:
            app.logger.warning(f"Email statut auto error: {e}")
    return True


def appliquer_case_a_taches(dossier_id, taxe, annee, mois, kind, declare, paye=False,
                            source='auto_checklist', acteur=None, email=True, accepte=False):
    """Déclarée -> en_cours ; acceptée/soldée -> terminee ; vidée -> a_faire.
    Retourne le nb de taches mises a jour."""
    nouveau_statut = 'a_faire'
    if declare:
        nouveau_statut = 'terminee' if (accepte or paye) else 'en_cours'
    n = 0
    for t in _taches_pour_case(dossier_id, taxe, annee, mois, kind):
        if t.statut == nouveau_statut:
            continue
        ancien_statut = t.statut
        t.statut = nouveau_statut
        t.statut_source = source if nouveau_statut != 'a_faire' else None
        if nouveau_statut == 'terminee':
            if not t.date_prise_en_charge:
                t.date_prise_en_charge = datetime.utcnow()
            if not t.date_completion:
                t.date_completion = datetime.utcnow()
        elif nouveau_statut == 'en_cours':
            if not t.date_prise_en_charge:
                t.date_prise_en_charge = datetime.utcnow()
            t.date_completion = None
        else:
            t.date_prise_en_charge = None
            t.date_completion = None
        notifier_changement_auto(t, source, nouveau_statut, acteur, email,
                                 ancien_statut=ancien_statut)
        n += 1
    return n


def appliquer_tache_a_case(tache, acteur=None):
    """Changement de statut d'une tache deadline -> reflete dans la checklist.
    en_cours/terminee -> case declaree (+paye si acompte termine) ; a_faire -> case videe.
    La ligne est conservee pour garder la trace : source,
    updated_by_id, date_modif — affichee dans l'infobulle de la grille.
    Retourne nb de cases mises a jour (0/1)."""
    m = case_pour_tache(tache)
    if not m or not tache.dossier_id:
        return 0
    taxe, annee, mois, kind, paye = m
    e = ChecklistEntry.query.filter_by(dossier_id=tache.dossier_id, taxe=taxe,
                                       annee=annee, mois=mois, kind=kind).first()
    if tache.statut not in ('terminee', 'a_faire', 'en_cours'):
        return 0
    if not e:
        e = ChecklistEntry(dossier_id=tache.dossier_id, taxe=taxe,
                           annee=annee, mois=mois, kind=kind)
        db.session.add(e)
    if tache.statut == 'terminee':
        e.declare = True
        e.paye = bool(paye)
    elif tache.statut == 'en_cours':
        e.declare = True
        e.paye = False
    else:
        e.declare = False
        e.paye = False
    e.pl_mode = False
    e.source = 'tache'
    if acteur is not None:
        e.updated_by_id = acteur.id
    e.date_modif = datetime.utcnow()
    return 1
