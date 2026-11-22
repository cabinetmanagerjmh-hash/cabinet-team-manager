"""Date de complétion : enregistrée sur tous les chemins et visible dans l'UI."""
from datetime import date


def _carte_liste(html, tid):
    """Extrait le bloc HTML de la carte portant cet identifiant de tâche."""
    marqueur = f'data-orbit-id="{tid}"'
    debut = html.index(marqueur)
    suite = html.find('data-orbit-id="', debut + len(marqueur))
    return html[debut:suite if suite != -1 else len(html)]


def _dossier_et_tache(flask_app, numero, titre='Dépôt TVA mensuel', echeance=None):
    from app import db
    from app.models import Dossier, Tache, User
    with flask_app.app_context():
        admin = User.query.filter_by(email='admin@test.local').first()
        d = Dossier(numero_dossier=numero, intitule='Date ' + numero, regime_tva='ca3')
        db.session.add(d)
        db.session.flush()
        t = Tache(titre=titre, dossier_id=d.id,
                  date_echeance=echeance or date(2026, 9, 24),
                  cree_par=admin.id, assigne_a=admin.id)
        db.session.add(t)
        db.session.commit()
        return d.id, t.id


def test_changement_manuel_enregistre_et_affiche_la_date(flask_app, seed, admin_client):
    from app.models import Tache
    _, tid = _dossier_et_tache(flask_app, 'D-DATE-001')

    r = admin_client.post(f'/changer_statut_tache/{tid}', data={'statut': 'terminee'})
    assert r.status_code == 302
    with flask_app.app_context():
        t = Tache.query.get(tid)
        assert t.statut == 'terminee'
        assert t.date_completion is not None
        jour = t.date_completion.strftime('%d/%m/%Y')
        complet = t.date_completion.strftime('%d/%m/%Y à %H:%M')

    liste = admin_client.get('/taches').get_data(as_text=True)
    assert f'terminée le {jour}' in _carte_liste(liste, tid)
    detail = admin_client.get(f'/vue_tache/{tid}').get_data(as_text=True)
    assert f'Terminée le {complet}' in detail


def test_reprise_efface_la_date(flask_app, seed, admin_client):
    from app.models import Tache
    _, tid = _dossier_et_tache(flask_app, 'D-DATE-002')

    admin_client.post(f'/changer_statut_tache/{tid}', data={'statut': 'terminee'})
    with flask_app.app_context():
        jour = Tache.query.get(tid).date_completion.strftime('%d/%m/%Y')

    r = admin_client.post(f'/changer_statut_tache/{tid}', data={'statut': 'en_cours'})
    assert r.status_code == 302
    with flask_app.app_context():
        assert Tache.query.get(tid).date_completion is None

    liste = admin_client.get('/taches').get_data(as_text=True)
    assert f'terminée le {jour}' not in _carte_liste(liste, tid)


def test_synchro_pennylane_affiche_la_date(flask_app, seed, admin_client):
    from app import db
    from app.models import Tache
    from app.checklist_link import appliquer_case_a_taches
    did, tid = _dossier_et_tache(flask_app, 'D-DATE-003')

    with flask_app.app_context():
        appliquer_case_a_taches(did, 'tva_mensuel', 2026, 8, 'depot', True,
                                source='auto_pennylane', accepte=True, email=False)
        db.session.commit()
        t = Tache.query.get(tid)
        assert t.statut == 'terminee'
        assert t.date_completion is not None
        complet = t.date_completion.strftime('%d/%m/%Y à %H:%M')

    detail = admin_client.get(f'/vue_tache/{tid}').get_data(as_text=True)
    assert f'Terminée le {complet}' in detail