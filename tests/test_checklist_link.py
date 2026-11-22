"""Liaison Checklist <-> taches deadline et origine du dernier changement.

- manuel         : statut changé dans l'application
- auto_checklist : case déclarée -> en cours ; case soldée -> terminée
- auto_pennylane : déclaration en cours -> en cours ; acceptée -> terminée
"""
from datetime import date


def _dossier_et_tache(flask_app, numero, titre='Dépôt TVA mensuel', echeance=None):
    """Crée un dossier + une tâche deadline (par défaut août 2026)."""
    from app import db
    from app.models import Dossier, Tache
    with flask_app.app_context():
        d = Dossier(numero_dossier=numero, intitule='Liaison ' + numero, regime_tva='ca3')
        db.session.add(d)
        db.session.flush()
        t = Tache(titre=titre, dossier_id=d.id, date_echeance=echeance or date(2026, 9, 24))
        db.session.add(t)
        db.session.commit()
        return d.id, t.id


def test_synchro_pennylane_en_cours_puis_acceptee(flask_app):
    from app import db
    from app.models import Tache
    from app.checklist_link import appliquer_case_a_taches
    did, tid = _dossier_et_tache(flask_app, 'D-LINK-001')
    with flask_app.app_context():
        n = appliquer_case_a_taches(did, 'tva_mensuel', 2026, 8, 'depot', True,
                                    source='auto_pennylane')
        db.session.commit()
        t = Tache.query.get(tid)
        assert n == 1
        assert t.statut == 'en_cours'
        assert t.statut_source == 'auto_pennylane'
        assert t.date_completion is None

        appliquer_case_a_taches(did, 'tva_mensuel', 2026, 8, 'depot', True,
                                source='auto_pennylane', accepte=True)
        db.session.commit()
        t = Tache.query.get(tid)
        assert t.statut == 'terminee'
        assert t.statut_source == 'auto_pennylane'
        assert t.date_completion is not None


def test_case_checklist_source_par_defaut_puis_remise_a_faire(flask_app):
    from app import db
    from app.models import Tache
    from app.checklist_link import appliquer_case_a_taches
    did, tid = _dossier_et_tache(flask_app, 'D-LINK-002')
    with flask_app.app_context():
        appliquer_case_a_taches(did, 'tva_mensuel', 2026, 8, 'depot', True)
        db.session.commit()
        t = Tache.query.get(tid)
        assert t.statut == 'en_cours'
        assert t.statut_source == 'auto_checklist'
        appliquer_case_a_taches(did, 'tva_mensuel', 2026, 8, 'depot', True, paye=True)
        db.session.commit()
        t = Tache.query.get(tid)
        assert t.statut == 'terminee'
        assert t.statut_source == 'auto_checklist'
        # case décochée -> la tâche repasse à faire et la source est effacée
        appliquer_case_a_taches(did, 'tva_mensuel', 2026, 8, 'depot', False)
        db.session.commit()
        t = Tache.query.get(tid)
        assert t.statut == 'a_faire'
        assert t.statut_source is None


def test_changement_manuel_source_visible(flask_app, admin_client):
    from app.models import Tache
    _, tid = _dossier_et_tache(flask_app, 'D-LINK-003', titre='Déclaration IS')
    r = admin_client.post(f'/changer_statut_tache/{tid}', data={'statut': 'en_cours'})
    assert r.status_code == 302
    with flask_app.app_context():
        t = Tache.query.get(tid)
        assert t.statut == 'en_cours'
        assert t.statut_source == 'manuel'
    page = admin_client.get(f'/vue_tache/{tid}')
    assert page.status_code == 200
    assert 'Manuel' in page.get_data(as_text=True)

    r = admin_client.post(f'/changer_statut_tache/{tid}', data={'statut': 'terminee'})
    assert r.status_code == 302
    with flask_app.app_context():
        t = Tache.query.get(tid)
        assert t.statut == 'terminee'
        assert t.statut_source == 'manuel'


def test_liste_taches_affiche_le_badge_source(flask_app, admin_client):
    from app import db
    from app.models import Tache
    _, tid = _dossier_et_tache(flask_app, 'D-LINK-004')
    with flask_app.app_context():
        t = Tache.query.get(tid)
        t.statut = 'en_cours'
        t.statut_source = 'auto_pennylane'
        db.session.commit()
    r = admin_client.get('/taches')
    assert r.status_code == 200
    html = r.get_data(as_text=True)
    assert 'Pennylane' in html
    assert 'Statut modifié automatiquement via Pennylane' in html


def test_notification_responsable_sur_changement_auto(flask_app, seed, monkeypatch):
    from app import db
    from app.models import Tache, Notification, User
    from app.checklist_link import appliquer_case_a_taches
    monkeypatch.setattr('app.integrations.brevo.send_email_via_brevo_api', lambda **kw: True)
    did, tid = _dossier_et_tache(flask_app, 'D-LINK-005')
    with flask_app.app_context():
        admin = User.query.filter_by(email='admin@test.local').first()
        Tache.query.get(tid).cree_par = admin.id
        db.session.commit()
        appliquer_case_a_taches(did, 'tva_mensuel', 2026, 8, 'depot', True, source='auto_pennylane')
        db.session.commit()
        notifs = Notification.query.filter_by(user_id=admin.id, tache_id=tid).all()
        assert len(notifs) == 1
        assert 'synchro Pennylane' in notifs[0].message
        assert notifs[0].type_notification == 'pennylane'
        # case decochee -> nouvelle notification « remise a faire »
        appliquer_case_a_taches(did, 'tva_mensuel', 2026, 8, 'depot', False)
        db.session.commit()
        notifs = Notification.query.filter_by(user_id=admin.id, tache_id=tid).order_by(Notification.id).all()
        assert len(notifs) == 2
        assert 'a faire' in notifs[-1].message


def test_pas_de_notification_si_le_responsable_est_l_acteur(flask_app, seed, monkeypatch):
    from app import db
    from app.models import Tache, Notification, User
    from app.checklist_link import appliquer_case_a_taches
    monkeypatch.setattr('app.integrations.brevo.send_email_via_brevo_api', lambda **kw: True)
    did, tid = _dossier_et_tache(flask_app, 'D-LINK-006')
    with flask_app.app_context():
        admin = User.query.filter_by(email='admin@test.local').first()
        Tache.query.get(tid).cree_par = admin.id
        db.session.commit()
        appliquer_case_a_taches(did, 'tva_mensuel', 2026, 8, 'depot', True, acteur=admin)
        db.session.commit()
        assert Tache.query.get(tid).statut == 'en_cours'
        assert Notification.query.filter_by(tache_id=tid).count() == 0


def test_sync_pennylane_notifie_createur_assigne_et_manager(flask_app, seed, monkeypatch):
    """Un changement de statut du a la synchro Pennylane doit etre visible dans
    l'application par ceux qui suivent la tache : createur, assigne, manager."""
    from app import db
    from app.models import Dossier, Equipe, Notification, Tache, User
    from app.checklist_link import appliquer_case_a_taches
    monkeypatch.setattr('app.integrations.brevo.send_email_via_brevo_api', lambda **kw: True)
    did, tid = _dossier_et_tache(flask_app, 'D-LINK-NOTIF')
    with flask_app.app_context():
        admin = User.query.filter_by(email='admin@test.local').first()
        membre = User.query.filter_by(email='membre@test.local').first()
        autre = User.query.filter_by(email='autre@test.local').first()
        equipe = Equipe(nom='Equipe Notif', manager_id=autre.id)
        db.session.add(equipe)
        db.session.flush()
        Dossier.query.get(did).equipe_id = equipe.id
        t = Tache.query.get(tid)
        t.cree_par = admin.id
        t.assigne_a = membre.id
        db.session.commit()

        appliquer_case_a_taches(did, 'tva_mensuel', 2026, 8, 'depot', True,
                                source='auto_pennylane')
        db.session.commit()
        notifs = Notification.query.filter_by(tache_id=tid).all()
        assert {n.user_id for n in notifs} == {admin.id, membre.id, autre.id}
        assert all(n.type_notification == 'pennylane' for n in notifs)
        assert all('synchro Pennylane' in n.message for n in notifs)
        assert all(n.lu is False for n in notifs)

        # acceptee -> terminee : chacun est prevenu une seconde fois
        appliquer_case_a_taches(did, 'tva_mensuel', 2026, 8, 'depot', True,
                                source='auto_pennylane', accepte=True)
        db.session.commit()
        assert Notification.query.filter_by(tache_id=tid).count() == 6
        assert Notification.query.filter_by(tache_id=tid, user_id=admin.id).count() == 2


def test_sync_pennylane_pas_de_doublon_quand_meme_personne(flask_app, seed, monkeypatch):
    """Createur = assigne = manager : une seule notification par changement."""
    from app import db
    from app.models import Dossier, Equipe, Notification, Tache, User
    from app.checklist_link import appliquer_case_a_taches
    monkeypatch.setattr('app.integrations.brevo.send_email_via_brevo_api', lambda **kw: True)
    did, tid = _dossier_et_tache(flask_app, 'D-LINK-NOTIF-2')
    with flask_app.app_context():
        admin = User.query.filter_by(email='admin@test.local').first()
        equipe = Equipe(nom='Equipe Solo', manager_id=admin.id)
        db.session.add(equipe)
        db.session.flush()
        Dossier.query.get(did).equipe_id = equipe.id
        t = Tache.query.get(tid)
        t.cree_par = admin.id
        t.assigne_a = admin.id
        db.session.commit()

        appliquer_case_a_taches(did, 'tva_mensuel', 2026, 8, 'depot', True,
                                source='auto_pennylane')
        db.session.commit()
        assert Notification.query.filter_by(tache_id=tid).count() == 1


def test_changement_checklist_reste_type_systeme(flask_app, seed, monkeypatch):
    """La synchro Pennylane a son type dedie ; la case Checklist reste 'systeme'."""
    from app import db
    from app.models import Notification, Tache, User
    from app.checklist_link import appliquer_case_a_taches
    monkeypatch.setattr('app.integrations.brevo.send_email_via_brevo_api', lambda **kw: True)
    did, tid = _dossier_et_tache(flask_app, 'D-LINK-NOTIF-3')
    with flask_app.app_context():
        admin = User.query.filter_by(email='admin@test.local').first()
        Tache.query.get(tid).cree_par = admin.id
        db.session.commit()
        appliquer_case_a_taches(did, 'tva_mensuel', 2026, 8, 'depot', True,
                                source='auto_checklist')
        db.session.commit()
        notifs = Notification.query.filter_by(tache_id=tid).all()
        assert len(notifs) == 1
        assert notifs[0].type_notification == 'systeme'


def test_sync_tva_octobre_2026_suit_le_statut_pennylane(flask_app, monkeypatch):
    from app import db
    from app.models import ChecklistEntry, Dossier, Tache, TvaStatutPennylane
    from app.integrations import pennylane_web as pw

    did, tid = _dossier_et_tache(
        flask_app, 'D-LINK-PL-1026', echeance=date(2026, 11, 24))
    company_id = 'company-tva-1026'
    with flask_app.app_context():
        Dossier.query.get(did).pennylane_customer_id = company_id
        db.session.commit()

    etat = {'statut': 'ongoing'}

    def fake_fetch(customer_id):
        if customer_id != company_id:
            return {'ok': True, 'vat_returns': [], 'future_vat_returns': []}
        return {'ok': True,
                'vat_returns': [{'period_start': '2026-10-01', 'status': etat['statut']}],
                'future_vat_returns': []}

    monkeypatch.setattr(pw, 'fetch_vat_forms', fake_fetch)
    import time
    monkeypatch.setattr(time, 'sleep', lambda *_: None)

    with flask_app.app_context():
        res = pw.sync_checklist_tva()
        assert res['ok'] is True
        t = Tache.query.get(tid)
        assert t.statut == 'en_cours'
        assert t.statut_source == 'auto_pennylane'
        assert t.date_completion is None
        e = ChecklistEntry.query.filter_by(
            dossier_id=did, taxe='tva_mensuel', annee=2026, mois=10, kind='depot').one()
        assert e.declare is True
        assert e.source == 'pennylane'
        st = TvaStatutPennylane.query.filter_by(dossier_id=did, annee=2026, mois=10).one()
        assert st.statut == 'ongoing'

        etat['statut'] = 'accepted'
        res = pw.sync_checklist_tva()
        assert res['ok'] is True
        t = Tache.query.get(tid)
        assert t.statut == 'terminee'
        assert t.statut_source == 'auto_pennylane'
        assert t.date_completion is not None
        assert TvaStatutPennylane.query.filter_by(
            dossier_id=did, annee=2026, mois=10).one().statut == 'accepted'
        Dossier.query.get(did).pennylane_customer_id = None
        db.session.commit()


def test_statuts_pennylane_declare_vs_accepte():
    from app.integrations.pennylane_web import ACCEPTED_STATUSES, EN_COURS_STATUSES
    assert {'filed', 'sent', 'transmitted', 'ongoing'} <= EN_COURS_STATUSES
    assert {'accepted', 'accepted_and_paid', 'paid'} <= ACCEPTED_STATUSES
    assert not (ACCEPTED_STATUSES & EN_COURS_STATUSES)
