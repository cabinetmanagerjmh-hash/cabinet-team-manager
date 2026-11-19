"""agenda — calendrier personnel par compte (tâches + rappels).

Vue type Google Agenda : chaque compte voit ses tâches (échéance et date de
traitement planifiée) et ses rappels dans un calendrier mois / semaine / jour.
Le périmètre est personnel : tâches qui me sont assignées ou que j'ai créées,
rappels qui me concernent. Les données sont fournies par /api/agenda.
"""
from flask import render_template, request, jsonify
from flask_login import login_required, current_user
from . import app, db
from .models import Tache, Rappel
from datetime import date, datetime, timedelta


@app.route('/agenda')
@login_required
def agenda():
    return render_template('agenda.html')


def _parse_date(brut, defaut):
    try:
        return datetime.strptime(brut, '%Y-%m-%d').date()
    except (ValueError, TypeError):
        return defaut


@app.route('/api/agenda')
@login_required
def api_agenda():
    today = date.today()
    debut = _parse_date(request.args.get('start'), today.replace(day=1))
    fin = _parse_date(request.args.get('end'), (debut + timedelta(days=31)).replace(day=1) - timedelta(days=1))
    if fin < debut:
        debut, fin = fin, debut

    mes_taches = Tache.query.filter(
        db.or_(Tache.assigne_a == current_user.id, Tache.cree_par == current_user.id)
    ).all()

    events = []
    for t in mes_taches:
        assigne = t.assigne.nom_complet() if t.assigne else 'Non assignée'
        createur = t.createur.nom_complet() if t.createur else None
        dossier = t.dossier.numero_dossier if t.dossier else None
        base = {
            'tache_id': t.id,
            'title': t.titre,
            'priority': t.priorite,
            'statut': t.statut,
            'assigne': assigne,
            'createur': createur,
            'dossier': dossier,
            'url': f'/vue_tache/{t.id}',
            'retard': t.est_en_retard(),
        }
        if t.date_echeance and debut <= t.date_echeance <= fin:
            events.append(dict(base, id=f't{t.id}-e', date=t.date_echeance.isoformat(), kind='echeance'))
        if t.date_planifiee and debut <= t.date_planifiee <= fin and t.date_planifiee != t.date_echeance:
            events.append(dict(base, id=f't{t.id}-p', date=t.date_planifiee.isoformat(), kind='planifie'))

    mes_rappels = Rappel.query.filter(
        Rappel.user_id == current_user.id,
        Rappel.archive == False,  # noqa: E712
        Rappel.date_echeance.between(debut, fin),
    ).all()
    for r in mes_rappels:
        events.append({
            'id': f'r{r.id}', 'date': r.date_echeance.isoformat(), 'kind': 'rappel',
            'title': r.titre, 'message': r.message, 'tache_id': None,
            'priority': None, 'statut': None, 'assigne': None, 'createur': None,
            'dossier': None, 'url': '/rappels', 'retard': r.date_echeance < today and not r.lu,
        })

    events.sort(key=lambda e: (e['date'], e['kind']))
    return jsonify({'ok': True, 'start': debut.isoformat(), 'end': fin.isoformat(), 'events': events})