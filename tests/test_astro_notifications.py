"""Notifications rédigées par ASTRO : validation stricte, repli et intégration."""
from datetime import date
from types import SimpleNamespace

from app.astro_notifications import PREFIXE, rediger_notification_statut


class _Prov:
    def __init__(self, configured=True, reponse=None):
        self.configured = configured
        self.reponse = reponse
        self.timeout = None
        self.appels = 0

    def is_configured(self):
        return self.configured

    def chat(self, messages, model=None, timeout=None):
        self.appels += 1
        self.timeout = timeout
        return self.reponse


class _Mgr:
    def __init__(self, prov):
        self.primary = prov
        self.fallback = _Prov(False)


def _tache():
    return SimpleNamespace(
        titre='Dépôt TVA mensuel',
        dossier=SimpleNamespace(numero_dossier='D-1'),
        date_echeance=date(2026, 9, 24),
    )


def _patch_manager(monkeypatch, prov):
    monkeypatch.setattr('app.integrations.llm_provider.get_llm_manager',
                        lambda: _Mgr(prov))


def test_astro_redige_une_notification_controlee(flask_app, monkeypatch):
    prov = _Prov(True, 'La tâche "Dépôt TVA mensuel" est maintenant terminée '
                       'via la synchro Pennylane.')
    _patch_manager(monkeypatch, prov)
    with flask_app.app_context():
        msg = rediger_notification_statut(_tache(), 'terminee', 'auto_pennylane',
                                          ancien_statut='en_cours', force=True)
    assert msg.startswith(PREFIXE)
    assert 'Dépôt TVA mensuel' in msg and 'terminée' in msg
    assert prov.timeout == 5


def test_astro_refuse_un_texte_sans_le_titre(flask_app, monkeypatch):
    prov = _Prov(True, 'La tâche est terminée via la synchro Pennylane.')
    _patch_manager(monkeypatch, prov)
    with flask_app.app_context():
        assert rediger_notification_statut(_tache(), 'terminee', 'auto_pennylane',
                                           force=True) is None


def test_astro_non_configure_repli_sans_appel(flask_app, monkeypatch):
    prov = _Prov(False)
    _patch_manager(monkeypatch, prov)
    with flask_app.app_context():
        assert rediger_notification_statut(_tache(), 'terminee', 'auto_pennylane',
                                           force=True) is None
    assert prov.appels == 0


def test_astro_ignore_en_mode_test_sans_force(flask_app, monkeypatch):
    prov = _Prov(True, 'La tâche "Dépôt TVA mensuel" est maintenant terminée.')
    _patch_manager(monkeypatch, prov)
    with flask_app.app_context():
        assert rediger_notification_statut(_tache(), 'terminee', 'auto_pennylane') is None
    assert prov.appels == 0


def test_notifier_utilise_le_message_astro(flask_app, seed, monkeypatch):
    from app import db
    from app.models import Dossier, Notification, Tache, User
    from app.checklist_link import appliquer_case_a_taches
    monkeypatch.setattr('app.astro_notifications.rediger_notification_statut',
                        lambda *a, **k: 'ASTRO · Message rédigé pour le test')
    with flask_app.app_context():
        admin = User.query.filter_by(email='admin@test.local').first()
        d = Dossier(numero_dossier='D-ASTRO-1', intitule='ASTRO', regime_tva='ca3')
        db.session.add(d)
        db.session.flush()
        t = Tache(titre='Dépôt TVA mensuel', dossier_id=d.id,
                  date_echeance=date(2026, 9, 24), cree_par=admin.id)
        db.session.add(t)
        db.session.commit()

        appliquer_case_a_taches(d.id, 'tva_mensuel', 2026, 8, 'depot', True,
                                source='auto_pennylane', email=False)
        db.session.commit()

        notif = Notification.query.filter_by(tache_id=t.id).one()
        assert notif.message == 'ASTRO · Message rédigé pour le test'