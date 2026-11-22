import html
import re
from datetime import datetime

import requests
import logging
from app.models import AppSetting

logger = logging.getLogger(__name__)

APP_URL = 'https://cabinet-team-manager.onrender.com'

# ---------------------------------------------------------------------------
# Mise en forme des emails — mêmes jetons que l'application (thème sombre)
# ---------------------------------------------------------------------------
_BG = '#000000'
_CARD = '#0a0d11'
_RAISED = '#0f141a'
_BAND = '#06080a'
_LINE = '#1e2731'
_INK = '#ffffff'
_TEXT = '#e9eff5'
_MUTED = '#8095a8'
_ACCENT = '#3a5a78'
_ACCENT_DEEP = '#1b3348'
_ACCENT_TXT = '#9db9cf'
_UI = "'Segoe UI','Helvetica Neue',Arial,sans-serif"
_MONO = "Consolas,'Liberation Mono',Menlo,monospace"
_SERIF = "Georgia,'Times New Roman',serif"

_URL_RE = re.compile(r'https?://[^\s<>"\']+')


def _esc(valeur) -> str:
    return html.escape('' if valeur is None else str(valeur), quote=True)


def _linkify(texte: str) -> str:
    """Échappe le texte et transforme les URL en liens azur."""
    bouts = []
    position = 0
    for trouve in _URL_RE.finditer(texte):
        url = trouve.group(0).rstrip('.,;:')
        fin = trouve.group(0)[len(url):]
        bouts.append(_esc(texte[position:trouve.start()]))
        bouts.append(
            f'<a href="{_esc(url)}" style="color:{_ACCENT_TXT};text-decoration:none;word-break:break-all;">'
            f'{_esc(url)}</a>'
        )
        bouts.append(_esc(fin))
        position = trouve.end()
    bouts.append(_esc(texte[position:]))
    return ''.join(bouts)


def _texte_nettoye(corps: str) -> list:
    """Découpe un corps texte en paragraphes, sans la signature finale."""
    texte = (corps or '').replace('\r\n', '\n').replace('\r', '\n').strip()
    lignes = texte.split('\n')
    while lignes and lignes[-1].strip() in ('', 'Cabinet JMH', 'JMH Orbit'):
        lignes.pop()
    return [p.strip() for p in '\n'.join(lignes).split('\n\n') if p.strip()]


def _html_from_text(corps: str) -> str:
    """Rend un corps texte (mails envoyés sans HTML) en paragraphes propres."""
    return ''.join(
        f'<p style="margin:0 0 14px 0;font-size:14px;line-height:1.65;color:{_TEXT};">'
        f'{_linkify(paragraphe).replace(chr(10), "<br>")}</p>'
        for paragraphe in _texte_nettoye(corps)
    )


def _apercu(corps: str) -> str:
    """Texte d'aperçu (preheader) affiché par les boîtes mail."""
    paragraphes = _texte_nettoye(corps)
    if not paragraphes:
        return ''
    plat = ' '.join(paragraphes[0].split())
    return plat[:140]


def _salutation(user) -> str:
    """Ouverture commune des mails : « Bonjour Prénom Nom, »."""
    return (
        f'<p style="margin:0 0 8px 0;font-size:15px;line-height:1.6;color:{_INK};">Bonjour '
        f'<strong style="font-weight:600;color:{_INK};">{_esc(user.prenom)} {_esc(user.nom)}</strong>,</p>'
    )


def _suite(html: str) -> str:
    """Phrase d'introduction sous la salutation."""
    return f'<p style="margin:0;font-size:13px;line-height:1.65;color:{_TEXT};">{html}</p>'


def _accent(texte) -> str:
    """Valeur mise en avant (nom, priorité, date) en blanc semi-gras."""
    return f'<span style="color:{_INK};font-weight:600;">{_esc(texte)}</span>'


def get_brevo_api_key() -> str:
    setting = AppSetting.query.filter_by(cle='BREVO_API_KEY').first()
    if setting and setting.valeur:
        return setting.valeur.strip()
    return ''


def send_email_via_brevo_api(
    to_email: str,
    subject: str,
    body: str,
    html_content: str = None,
    sender_name: str = 'Cabinet JMH',
) -> bool:
    # Tout mail part avec une version HTML mise en forme : si l'appelant n'en
    # fournit pas, le corps texte est rendu dans la charte de l'application.
    corps = body or ''
    if not html_content:
        html_content = _email_layout(
            header_title=subject or 'Notification',
            intro_html=_html_from_text(corps),
            preheader=_apercu(corps),
            sent_note='Message envoyé depuis JMH Orbit.',
        )

    api_key = get_brevo_api_key()
    if not api_key:
        logger.error('BREVO_API_KEY is not configured, falling back to SMTP')
        return send_email_notification_fallback(to_email, subject, corps, html_content)

    url = 'https://api.brevo.com/v3/smtp/email'
    headers = {
        'api-key': api_key,
        'Content-Type': 'application/json',
    }
    payload = {
        'sender': {
            'name': sender_name,
            'email': 'cabinet.manager.jmh@gmail.com',
        },
        'to': [{'email': to_email}],
        'subject': subject,
        'textContent': corps,
        'htmlContent': html_content,
    }

    try:
        resp = requests.post(url, json=payload, headers=headers, timeout=20)
        if resp.status_code in (200, 201):
            logger.info('Brevo API email sent to %s', to_email)
            return True
        logger.error('Brevo API error: %s %s', resp.status_code, resp.text)
        return False
    except Exception as e:
        logger.error('Brevo API request failed: %s', e)
        return False


def _email_layout(header_title, intro_html, rows=None, rows_html_override=None,
                  cta_url=None, cta_text=None, footer_note='', preheader='',
                  sent_note="Cet email a été envoyé automatiquement par l'application de gestion."):
    """Gabarit email unique, en tableaux (Outlook/Gmail/Apple Mail).

    Identité : noir profond, filets bleu marine, texte blanc ; titres en serif,
    étiquettes et méta en mono capitales.
    """
    titre = _esc(header_title)
    date_envoi = datetime.now().strftime('%d.%m.%Y')

    preheader_html = ''
    if preheader:
        preheader_html = (
            f'<div style="display:none;font-size:1px;line-height:1px;max-height:0;max-width:0;'
            f'opacity:0;overflow:hidden;color:{_BG};">{_esc(preheader)}</div>'
        )

    content_html = intro_html or ''
    if rows_html_override:
        content_html += rows_html_override
    elif rows:
        lignes = ''
        for i, (label, valeur) in enumerate(rows):
            bordure = '' if i == 0 else f'border-top:1px solid {_LINE};'
            lignes += (
                f'<tr>'
                f'<td style="padding:10px 0;{bordure}font-family:{_MONO};font-size:9px;letter-spacing:.14em;'
                f'text-transform:uppercase;color:{_MUTED};vertical-align:top;white-space:nowrap;">{label}</td>'
                f'<td style="padding:10px 0 10px 20px;{bordure}font-size:14px;line-height:1.5;'
                f'color:{_INK};vertical-align:top;word-break:break-word;">{valeur}</td>'
                f'</tr>'
            )
        content_html += (
            f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" '
            f'style="width:100%;border-collapse:collapse;margin:18px 0 4px 0;">'
            f'<tr><td style="padding:6px 20px;background:{_RAISED};border:1px solid {_LINE};'
            f'border-left:2px solid {_ACCENT};border-radius:4px;">'
            f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" '
            f'style="width:100%;border-collapse:collapse;">{lignes}</table>'
            f'</td></tr></table>'
        )

    cta_html = ''
    if cta_url and cta_text:
        lien = _esc(cta_url)
        cta_html = (
            f'<table role="presentation" cellpadding="0" cellspacing="0" border="0" align="center" '
            f'style="margin:26px auto 0 auto;">'
            f'<tr><td align="center" style="background:{_ACCENT_DEEP};border:1px solid {_ACCENT};'
            f'border-radius:4px;">'
            f'<a href="{lien}" style="display:inline-block;padding:14px 34px;font-family:{_UI};'
            f'font-size:12px;font-weight:600;letter-spacing:.14em;text-transform:uppercase;'
            f'color:{_INK};text-decoration:none;">{_esc(cta_text)}</a>'
            f'</td></tr></table>'
            f'<p style="margin:12px 0 0 0;font-family:{_MONO};font-size:10px;line-height:1.6;'
            f'color:{_MUTED};text-align:center;">'
            f'Lien direct : <a href="{lien}" style="color:{_ACCENT_TXT};text-decoration:none;'
            f'word-break:break-all;">{lien}</a></p>'
        )

    note_html = ''
    if footer_note:
        note_html = (
            f'<p style="margin:24px 0 0 0;padding-top:16px;border-top:1px solid {_LINE};'
            f'font-size:12px;line-height:1.65;color:{_MUTED};">{footer_note}</p>'
        )

    monogramme = (
        f'<table role="presentation" cellpadding="0" cellspacing="0" border="0" '
        f'style="border-collapse:collapse;">'
        f'<tr><td width="40" height="40" align="center" valign="middle" '
        f'style="width:40px;height:40px;background:{_RAISED};border:1px solid {_ACCENT};'
        f'font-family:{_MONO};font-size:12px;font-weight:700;letter-spacing:.04em;'
        f'color:{_INK};">JMH</td></tr></table>'
    )

    return (
        '<!DOCTYPE html>\n<html lang="fr">\n<head>\n'
        '<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width,initial-scale=1">\n'
        '<meta name="color-scheme" content="dark">\n'
        '<meta name="supported-color-schemes" content="dark">\n'
        f'<title>{titre}</title>\n'
        '</head>\n'
        f'<body style="margin:0;padding:0;background:{_BG};-webkit-text-size-adjust:100%;">'
        f'{preheader_html}'
        f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" '
        f'style="width:100%;background:{_BG};border-collapse:collapse;">'
        '<tr><td align="center" style="padding:32px 16px;">'
        f'<table role="presentation" width="600" cellpadding="0" cellspacing="0" border="0" '
        f'style="width:100%;max-width:600px;background:{_CARD};border:1px solid {_LINE};'
        f'border-top:3px solid {_ACCENT};border-collapse:separate;">'

        # Bandeau : monogramme, produit, date d'envoi
        '<tr><td style="padding:22px 32px;border-bottom:1px solid ' + _LINE + ';">'
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" '
        'style="width:100%;border-collapse:collapse;"><tr>'
        f'<td width="40" style="width:40px;vertical-align:middle;">{monogramme}</td>'
        '<td style="padding-left:14px;vertical-align:middle;">'
        f'<div style="font-family:{_UI};font-size:11px;font-weight:600;letter-spacing:.22em;'
        f'text-transform:uppercase;color:{_INK};">JMH Orbit</div>'
        f'<div style="margin-top:5px;font-family:{_MONO};font-size:9px;letter-spacing:.14em;'
        f'text-transform:uppercase;color:{_MUTED};">Cabinet JMH — Expertise comptable</div>'
        '</td>'
        f'<td align="right" style="vertical-align:middle;font-family:{_MONO};font-size:11px;'
        f'letter-spacing:.1em;color:{_MUTED};white-space:nowrap;">{date_envoi}</td>'
        '</tr></table>'
        '</td></tr>'

        # Titre + filet
        '<tr><td style="padding:28px 32px 0 32px;">'
        f'<h1 style="margin:0;font-family:{_SERIF};font-weight:400;font-size:24px;line-height:1.3;'
        f'color:{_INK};">{titre}</h1>'
        f'<div style="width:36px;height:2px;background:{_ACCENT};margin-top:14px;'
        'font-size:0;line-height:0;">&nbsp;</div>'
        '</td></tr>'

        # Corps
        '<tr><td style="padding:14px 32px 30px 32px;'
        f'font-family:{_UI};font-size:14px;line-height:1.65;color:{_TEXT};">'
        f'{content_html}{cta_html}{note_html}'
        '</td></tr>'

        # Pied
        '<tr><td style="padding:18px 32px;background:' + _BAND + ';border-top:1px solid ' + _LINE + ';">'
        f'<p style="margin:0;font-family:{_MONO};font-size:9px;letter-spacing:.14em;'
        f'text-transform:uppercase;line-height:1.7;color:{_MUTED};">'
        'Cabinet JMH · Expertise comptable · JMH Orbit</p>'
        f'<p style="margin:6px 0 0 0;font-family:{_UI};font-size:10px;line-height:1.6;'
        f'color:{_MUTED};">{_esc(sent_note)}</p>'
        '</td></tr>'

        '</table>'
        '</td></tr></table>'
        '</body>\n</html>'
    )


def send_task_assigned_email_brevo(tache, assignee_id: int) -> bool:
    from app.models import User
    from flask import render_template_string

    user = User.query.get(assignee_id)
    if not user or not user.email:
        return False

    createur = tache.createur if tache.cree_par else None
    createur_nom = createur.nom_complet() if createur else None
    par_qui = f' par {createur_nom}' if createur_nom and tache.cree_par != assignee_id else ''

    subject = f"Nouvelle tâche assignée{par_qui} : {tache.titre}"

    template = _email_layout(
        header_title="Nouvelle tâche assignée",
        intro_html=(
            _salutation(user)
            + _suite(f'Une nouvelle tâche vous a été assignée{par_qui} :')
        ),
        rows=[
            ('Tâche', tache.titre),
            ('Assignée par', createur_nom or '—'),
            ('Description', tache.description or '(aucune)'),
            ('Priorité', _accent(tache.priorite.capitalize())),
            ('Échéance', tache.date_echeance.strftime('%d/%m/%Y') if tache.date_echeance else '—'),
        ],
        cta_url=f'{APP_URL}/taches',
        cta_text='Voir mes tâches',
        footer_note="Ouvrez l'application pour prendre en charge cette tâche dès que possible.",
    )

    return send_email_via_brevo_api(
        to_email=user.email,
        subject=subject,
        body=f"Bonjour {user.prenom} {user.nom}, nouvelle tâche{par_qui} : {tache.titre}. Consultez vos tâches sur {APP_URL}/taches",
        html_content=template,
    )


def send_tache_creee_manager_email_brevo(tache, manager_id: int, auteur: str) -> bool:
    """Email au manager quand un collaborateur cree une tache urgente."""
    from app.models import User

    manager = User.query.get(manager_id)
    if not manager or not manager.email:
        return False

    assigne = User.query.get(tache.assigne_a) if tache.assigne_a else None
    if assigne and assigne.id == manager.id:
        cible = 'vous'
    elif assigne:
        cible = f'{assigne.prenom} {assigne.nom}'
    else:
        cible = '—'

    subject = f"Tâche urgente créée par {auteur} : {tache.titre}"
    template = _email_layout(
        header_title="Tâche urgente dans votre équipe",
        intro_html=(
            _salutation(manager)
            + _suite(f'{_esc(auteur)} a créé une tâche de priorité haute :')
        ),
        rows=[
            ('Tâche', tache.titre),
            ('Assignée à', cible),
            ('Priorité', _accent(tache.priorite.capitalize())),
            ('Échéance', tache.date_echeance.strftime('%d/%m/%Y') if tache.date_echeance else '—'),
        ],
        cta_url=f'{APP_URL}/taches',
        cta_text='Suivre l\'avancement',
        footer_note="Tâche créée par un collaborateur de votre équipe.",
    )

    return send_email_via_brevo_api(
        to_email=manager.email,
        subject=subject,
        body=f"Bonjour {manager.prenom}, {auteur} a créé une tâche urgente : {tache.titre}. "
             f"Suivi sur {APP_URL}/taches",
        html_content=template,
    )


def send_tache_planifiee_email_brevo(tache, dest_user, assigne_nom: str, date_fr: str) -> bool:
    """Email à l'assigneur quand l'assigné planifie une date de traitement."""
    if not dest_user or not dest_user.email:
        return False

    subject = f"Traitement planifié le {date_fr} : {tache.titre}"
    template = _email_layout(
        header_title="Traitement planifié",
        intro_html=(
            _salutation(dest_user)
            + _suite(f'{_accent(assigne_nom)} a planifié de traiter la tâche que vous lui avez assignée :')
        ),
        rows=[
            ('Tâche', tache.titre),
            ('Date planifiée', _accent(date_fr)),
            ('Échéance', tache.date_echeance.strftime('%d/%m/%Y') if tache.date_echeance else '—'),
        ],
        cta_url=f'{APP_URL}/vue_tache/{tache.id}',
        cta_text='Voir la tâche',
        footer_note="La date planifiée est un engagement de traitement, distinct de l'échéance.",
    )

    return send_email_via_brevo_api(
        to_email=dest_user.email,
        subject=subject,
        body=f"Bonjour {dest_user.prenom}, {assigne_nom} a planifié de traiter \"{tache.titre}\" le {date_fr}. "
             f"Détail sur {APP_URL}/vue_tache/{tache.id}",
        html_content=template,
    )


def send_task_taken_email_brevo(tache, collab_nom) -> bool:
    from app.models import User
    from flask import render_template_string

    creeur = User.query.get(tache.cree_par)
    if not creeur or not creeur.email:
        return False

    subject = f"Prise en charge : {tache.titre}"
    date_prise = tache.date_prise_en_charge.strftime('%d/%m/%Y %H:%M') if tache.date_prise_en_charge else "Aujourd'hui"

    template = _email_layout(
        header_title="Tâche prise en charge",
        intro_html=(
            _salutation(creeur)
            + _suite(f'{_accent(collab_nom)} a pris en charge la tâche :')
        ),
        rows=[
            ('Tâche', tache.titre),
            ('Collaborateur', _accent(collab_nom)),
            ('Début', date_prise),
        ],
        cta_url=f'{APP_URL}/taches',
        cta_text='Voir les tâches',
        footer_note="Suivez l'avancement de cette tâche dans l'application.",
    )

    return send_email_via_brevo_api(
        to_email=creeur.email,
        subject=subject,
        body=f"Bonjour {creeur.prenom}, {collab_nom} a pris en charge la tâche : {tache.titre}. Consultez sur {APP_URL}/taches",
        html_content=template,
    )


def send_task_completed_email_brevo(tache, collab_nom) -> bool:
    from app.models import User
    from flask import render_template_string

    creeur = User.query.get(tache.cree_par)
    if not creeur or not creeur.email:
        return False

    subject = f"Tâche terminée : {tache.titre}"
    date_fin = tache.date_completion.strftime('%d/%m/%Y %H:%M') if tache.date_completion else "Aujourd'hui"

    template = _email_layout(
        header_title="Tâche terminée",
        intro_html=(
            _salutation(creeur)
            + _suite(f'{_accent(collab_nom)} a terminé la tâche :')
        ),
        rows=[
            ('Tâche', tache.titre),
            ('Terminée par', _accent(collab_nom)),
            ('Date de fin', date_fin),
        ],
        cta_url=f'{APP_URL}/taches',
        cta_text='Voir les tâches',
        footer_note="Consultez le suivi de vos dossiers dans l'application.",
    )

    return send_email_via_brevo_api(
        to_email=creeur.email,
        subject=subject,
        body=f"Bonjour {creeur.prenom}, {collab_nom} a terminé la tâche : {tache.titre}. Consultez sur {APP_URL}/taches",
        html_content=template,
    )


def send_daily_digest_email(user, tasks, date_str=''):
    """Envoie UN email récapitulatif des tâches arrivant à échéance à un destinataire."""
    if not user or not user.email or not tasks:
        return False

    subject = f"{len(tasks)} tâche(s) à échéance aujourd'hui — Cabinet JMH"

    rows = ''
    for t in tasks[:15]:
        dossier_nom = f"{t.dossier.numero_dossier}" if t.dossier else '—'
        statut_label = {'a_faire': 'À faire', 'en_cours': 'En cours', 'terminee': 'Terminée'}.get(t.statut, t.statut)
        rows += (
            f'<tr style="border-bottom:1px solid {_LINE};">'
            f'<td style="padding:9px 0;color:{_INK};font-size:13px;">{t.titre}</td>'
            f'<td style="padding:9px 0 9px 12px;color:{_INK};font-family:{_MONO};font-size:11px;white-space:nowrap;">{dossier_nom}</td>'
            f'<td style="padding:9px 0 9px 12px;color:{_MUTED};font-size:11px;white-space:nowrap;">{statut_label}</td>'
            f'</tr>'
        )
    if len(tasks) > 15:
        rows += (f'<tr><td colspan="3" style="padding:9px 0;color:{_MUTED};font-size:11px;">'
                 f'… et {len(tasks) - 15} autre(s) tâche(s)</td></tr>')

    entete = (f'color:{_MUTED};font-family:{_MONO};font-size:10px;letter-spacing:.08em;'
              f'text-transform:uppercase;padding:9px 0;border-bottom:1px solid {_LINE};')
    template = _email_layout(
        header_title="Rappel des échéances du jour",
        intro_html=(
            _salutation(user)
            + _suite(f'Voici les {_accent(len(tasks))} tâche(s) à échéance {_esc(date_str)} :')
        ),
        rows_html_override=(
            f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" '
            f'style="width:100%;border-collapse:collapse;margin-top:16px;">'
            f'<tr><td style="{entete}">Tâche</td>'
            f'<td style="{entete}padding-left:12px;">Dossier</td>'
            f'<td style="{entete}padding-left:12px;">Statut</td></tr>'
            f'{rows}'
            f'</table>'
        ),
        cta_url=f'{APP_URL}/taches',
        cta_text='Voir mes tâches',
        footer_note="Pensez à mettre à jour le statut de vos tâches une fois traitées.",
    )

    return send_email_via_brevo_api(
        to_email=user.email,
        subject=subject,
        body=f"Bonjour {user.prenom}, vous avez {len(tasks)} tâche(s) à échéance aujourd'hui. Consultez sur {APP_URL}/taches",
        html_content=template,
    )


def send_pennylane_new_docs_email_brevo(dossier, nouveaux: list, destinataire_id: int) -> bool:
    """Email « nouveaux documents Pennylane » (ventes/achats/transactions détectées).

    Envoyé au manager + collaborateur du dossier dès détection (cron horaire
    verifier_nouveaux_pennylane + bouton Vérifier maintenant).
    """
    from app.models import User

    user = User.query.get(destinataire_id)
    if not user or not user.email:
        return False

    n_ventes = sum(1 for n in nouveaux if n['type'] == 'facture_vente')
    n_achats = sum(1 for n in nouveaux if n['type'] == 'facture_achat')
    n_tx = sum(1 for n in nouveaux if n['type'] == 'transaction')
    parts = []
    if n_ventes: parts.append(f"{n_ventes} facture(s) de vente")
    if n_achats: parts.append(f"{n_achats} facture(s) d'achat")
    if n_tx: parts.append(f"{n_tx} transaction(s) bancaire(s)")
    resume = ' + '.join(parts) or f"{len(nouveaux)} document(s)"

    # Détail : max 8 lignes dans l'email pour rester lisible
    rows_detail = []
    for n in nouveaux[:8]:
        type_label = {'facture_vente': 'Vente', 'facture_achat': 'Achat', 'transaction': 'Banque'}.get(n['type'], n['type'])
        montant = f"{n['montant']:,.2f}".replace(',', ' ') + ' €' if n.get('montant') is not None else '—'
        rows_detail.append((type_label, f"{n.get('reference') or '(sans référence)'} — {montant}"))

    subject = f"Pennylane {dossier.numero_dossier} — {resume}"

    template = _email_layout(
        header_title="Nouveaux documents Pennylane",
        intro_html=(
            _salutation(user)
            + _suite(
                f'{len(nouveaux)} nouveau(x) document(s) viennent d\'être détecté(s) sur Pennylane '
                f'pour le dossier {_accent(dossier.numero_dossier)} :'
            )
        ),
        rows=rows_detail,
        cta_url=f"{APP_URL}/pennylane/dossier/{dossier.id}",
        cta_text='Voir dans l\'application',
        footer_note="Vous recevez cet email car vous êtes manager ou collaborateur de ce dossier.",
    )

    return send_email_via_brevo_api(
        to_email=user.email,
        subject=subject,
        body=f"Bonjour {user.prenom}, {resume} nouveau(x) document(s) Pennylane sur le dossier {dossier.numero_dossier}. "
             f"Consultez : {APP_URL}/pennylane/dossier/{dossier.id}",
        html_content=template,
    )


def send_pennylane_digest_email_brevo(user, par_dossier, creneau: str) -> bool:
    """Recapitulatif groupe des nouveaux documents Pennylane (un mail par destinataire et par creneau).

    par_dossier : liste de tuples (dossier, [PennylaneItem, ...]).
    """
    from html import escape
    if not user or not user.email or not par_dossier:
        return False

    labels = {'facture_vente': 'Vente', 'facture_achat': 'Achat', 'transaction': 'Banque'}
    total = sum(len(items) for _, items in par_dossier)
    rows = []
    for dossier, items in par_dossier:
        lignes = []
        for it in items[:8]:
            montant = (f"{it.montant:,.2f}".replace(',', ' ') + ' €') if it.montant is not None else '—'
            lignes.append(
                f"{escape(labels.get(it.item_type, it.item_type))} — "
                f"{escape(it.reference or '(sans référence)')} — {montant}"
            )
        if len(items) > 8:
            lignes.append(f"… et {len(items) - 8} autre(s)")
        rows.append((escape(dossier.numero_dossier), '<br>'.join(lignes)))

    template = _email_layout(
        header_title=f"Récapitulatif Pennylane — {creneau}",
        intro_html=(
            _salutation(user)
            + _suite(f'{_accent(total)} nouveau(x) document(s) détecté(s) sur Pennylane '
                     f'depuis le dernier récapitulatif :')
        ),
        rows=rows,
        cta_url=f"{APP_URL}/pennylane",
        cta_text="Ouvrir Pennylane",
        footer_note="Récapitulatif envoyé selon les créneaux définis dans Paramètres. Vous recevez cet email car vous êtes manager ou collaborateur de ces dossiers.",
    )

    return send_email_via_brevo_api(
        to_email=user.email,
        subject=f"{total} nouveau(x) document(s) Pennylane — récapitulatif {creneau}",
        body=f"Bonjour {user.prenom}, {total} nouveau(x) document(s) Pennylane détecté(s). Consultez : {APP_URL}/pennylane",
        html_content=template,
    )


def send_email_notification_fallback(to_email, subject, body, html_content=None):
    """Fallback to SMTP if Brevo is not configured."""
    try:
        from app.routes_notifications import get_mail_config
        import smtplib
        from email.mime.text import MIMEText
        from email.mime.multipart import MIMEMultipart
        config = get_mail_config()
        username = config['MAIL_USERNAME']
        password = config['MAIL_PASSWORD']
        if not username or not password:
            logger.error('SMTP fallback: MAIL_USERNAME/PASSWORD not configured')
            return False
        msg = MIMEMultipart('alternative')
        msg['From'] = config.get('MAIL_DEFAULT_SENDER', username)
        msg['To'] = to_email
        msg['Subject'] = subject
        if html_content:
            msg.attach(MIMEText(body, 'plain'))
            msg.attach(MIMEText(html_content, 'html'))
        else:
            msg.attach(MIMEText(body, 'plain'))
        server_host = config['MAIL_SERVER']
        server_port = int(config['MAIL_PORT'])
        server = smtplib.SMTP(server_host, server_port)
        server.ehlo()
        server.starttls()
        server.login(username, password)
        server.send_message(msg)
        server.quit()
        logger.info('SMTP fallback email sent to %s', to_email)
        return True
    except Exception as e:
        logger.error('SMTP fallback failed: %s', e)
        return False
