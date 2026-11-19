"""Planification d'une date de traitement + agenda personnel.

Demande Aziz :
- l'assigné fixe la date à laquelle il traitera la tâche ; la personne qui la
  lui a assignée reçoit une notification (et un email) ;
- chaque compte dispose d'un agenda (tâches : échéance + date planifiée, rappels).
"""
import pytest
from datetime import date, timedelta


def _tache(flask_app, tid):
    from app.models import Tache
    with flask_app.app_context():
        return Tache.query.get(tid)


# ---------------------------------------------------------------------------
# Planifier
# ---------------------------------------------------------------------------

def test_planifier_notifie_le_createur(flask_app, seed, membre_client, monkeypatch):
    appels = []
    monkeypatch.setattr('app.integrations.brevo.send_tache_planifiee_email_brevo',
                        lambda t, dest, nom, dfr: appels.append((dest.id, nom, dfr)) or True)
    tid = seed['t1']  # assignée à membre, créée par admin
    r = membre_client.post(f'/planifier_tache/{tid}', data={'date_planifiee': '2030-01-15'})
    assert r.status_code == 302
    with flask_app.app_context():
        from app.models import Tache, Notification, User
        assert Tache.query.get(tid).date_planifiee == date(2030, 1, 15)
        admin_id = User.query.filter_by(email='admin@test.local').first().id
        notifs = Notification.query.filter_by(user_id=admin_id, tache_id=tid,
                                              type_notification='planification').all()
        assert notifs, 'le créateur doit être notifié'
        assert 'a planifié de traiter' in notifs[0].message
        assert '15/01/2030' in notifs[0].message
        assert 'Marie Dupont' in notifs[0].message
    assert appels and appels[0][2] == '15/01/2030', 'email au créateur'


def test_planifier_refuse_pour_non_assigne(flask_app, seed, membre_client):
    tid = seed['t2']  # assignée à autre, pas à membre
    r = membre_client.post(f'/planifier_tache/{tid}', data={'date_planifiee': '2030-01-15'})
    assert r.status_code == 302
    assert _tache(flask_app, tid).date_planifiee is None


def test_planifier_date_vide_retire_la_planification(flask_app, seed, membre_client, monkeypatch):
    monkeypatch.setattr('app.integrations.brevo.send_tache_planifiee_email_brevo',
                        lambda t, dest, nom, dfr: True)
    tid = seed['t1']
    membre_client.post(f'/planifier_tache/{tid}', data={'date_planifiee': '2030-02-01'})
    assert _tache(flask_app, tid).date_planifiee == date(2030, 2, 1)
    membre_client.post(f'/planifier_tache/{tid}', data={'date_planifiee': ''})
    assert _tache(flask_app, tid).date_planifiee is None


def test_planifier_date_invalide_ignoree(flask_app, seed, membre_client):
    tid = seed['t1']
    membre_client.post(f'/planifier_tache/{tid}', data={'date_planifiee': 'pas-une-date'})
    assert _tache(flask_app, tid).date_planifiee is None


# ---------------------------------------------------------------------------
# Notification d'assignation : mentionne qui a assigné
# ---------------------------------------------------------------------------

def test_assignation_notification_mentionne_le_createur(flask_app, seed, admin_client):
    from app.models import User
    with flask_app.app_context():
        membre_id = User.query.filter_by(email='membre@test.local').first().id
    r = admin_client.post('/taches', data={'titre': 'Tache origine test',
                                           'assigne_a': str(membre_id),
                                           'priorite': 'moyenne', 'statut': 'a_faire',
                                           'date_echeance': '2030-12-01'})
    assert r.status_code == 302
    with flask_app.app_context():
        from app.models import Notification
        notifs = Notification.query.filter_by(user_id=membre_id).all()
        assert any('assignée par Admin Orbit' in n.message and 'Tache origine test' in n.message
                   for n in notifs)


# ---------------------------------------------------------------------------
# Agenda
# ---------------------------------------------------------------------------

def test_agenda_page_rendue(flask_app, seed, membre_client):
    r = membre_client.get('/agenda')
    assert r.status_code == 200
    html = r.get_data(as_text=True)
    assert 'id="agCalendar"' in html
    assert '/api/agenda' in html


def test_agenda_api_echeance_et_planifie(flask_app, seed, membre_client, monkeypatch):
    monkeypatch.setattr('app.integrations.brevo.send_tache_planifiee_email_brevo',
                        lambda t, dest, nom, dfr: True)
    tid = seed['t1']
    debut = date.today()
    fin = debut + timedelta(days=30)
    q = f'?start={debut.isoformat()}&end={fin.isoformat()}'
    d = membre_client.get('/api/agenda' + q).get_json()
    assert d['ok'] is True
    ech = [e for e in d['events'] if e['tache_id'] == tid and e['kind'] == 'echeance']
    assert ech, 'échéance de la tâche dans la plage'
    assert ech[0]['url'] == f'/vue_tache/{tid}'
    # pas de date planifiée -> pas d'événement planifie
    assert not [e for e in d['events'] if e['tache_id'] == tid and e['kind'] == 'planifie']
    # poser une date planifiée dans la plage -> événement planifie
    plan = (debut + timedelta(days=2)).isoformat()
    membre_client.post(f'/planifier_tache/{tid}', data={'date_planifiee': plan})
    d2 = membre_client.get('/api/agenda' + q).get_json()
    pl = [e for e in d2['events'] if e['tache_id'] == tid and e['kind'] == 'planifie']
    assert pl and pl[0]['date'] == plan
    # nettoyage
    membre_client.post(f'/planifier_tache/{tid}', data={'date_planifiee': ''})


def test_agenda_api_perimetre_personnel(flask_app, seed, membre_client):
    debut = date.today()
    fin = debut + timedelta(days=30)
    d = membre_client.get(f'/api/agenda?start={debut.isoformat()}&end={fin.isoformat()}').get_json()
    # t2 est assignée à autre et créée par admin : invisible pour membre
    assert not [e for e in d['events'] if e['tache_id'] == seed['t2']]