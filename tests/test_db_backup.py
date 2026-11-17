"""Sauvegarde / restauration de la base : stats, export .sql, import round-trip."""
import io

from sqlalchemy import Boolean, LargeBinary, String


# --- Routes ---------------------------------------------------------------

def test_stats_admin(admin_client):
    r = admin_client.get('/api/backup/stats')
    assert r.status_code == 200
    data = r.get_json()
    assert data['ok'] is True
    assert data['backend'] == 'sqlite'
    tables = {t['table'] for t in data['tables']}
    assert {'users', 'dossiers', 'taches'} <= tables
    assert data['total'] >= 7  # seed : 3 membres + 2 dossiers + 2 taches
    assert 'alembic_version' not in tables


def test_stats_membre_refuse(membre_client):
    assert membre_client.get('/api/backup/stats').status_code == 403


def test_export_admin(admin_client):
    r = admin_client.get('/settings/export-base')
    assert r.status_code == 200
    assert r.mimetype == 'application/sql'
    assert 'attachment' in r.headers.get('Content-Disposition', '')
    txt = r.data.decode('utf-8')
    assert '-- JMH Orbit backup v1' in txt
    assert 'INSERT INTO "users"' in txt
    assert 'INSERT INTO "alembic_version"' not in txt


def test_export_membre_redirige(membre_client):
    r = membre_client.get('/settings/export-base')
    assert r.status_code == 302


def test_import_confirmation_refusee(admin_client):
    r = admin_client.post('/api/backup/import', data={'confirmation': 'OUI'})
    assert r.status_code == 400


def test_import_fichier_manquant(admin_client):
    r = admin_client.post('/api/backup/import',
                          data={'confirmation': 'RESTAURER'})
    assert r.status_code == 400


def test_import_fichier_etranger(admin_client):
    r = admin_client.post('/api/backup/import',
                          data={'fichier': (io.BytesIO(b'DROP TABLE users;'), 'x.sql'),
                                'confirmation': 'RESTAURER'},
                          content_type='multipart/form-data')
    assert r.status_code == 400


def test_import_membre_refuse(membre_client):
    r = membre_client.post('/api/backup/import',
                           data={'fichier': (io.BytesIO(b''), 'x.sql'),
                                 'confirmation': 'RESTAURER'},
                           content_type='multipart/form-data')
    assert r.status_code == 403


def test_import_roundtrip(admin_client):
    """Export -> import -> le contenu de la base est identique."""
    avant = admin_client.get('/api/backup/stats').get_json()
    dump = admin_client.get('/settings/export-base').data
    r = admin_client.post('/api/backup/import',
                          data={'fichier': (io.BytesIO(dump), 'sauvegarde.sql'),
                                'confirmation': 'RESTAURER'},
                          content_type='multipart/form-data')
    assert r.status_code == 200, r.get_json()
    assert r.get_json()['ok'] is True
    apres = admin_client.get('/api/backup/stats').get_json()
    assert apres['total'] == avant['total']
    avant_tables = {t['table']: t['lignes'] for t in avant['tables']}
    apres_tables = {t['table']: t['lignes'] for t in apres['tables']}
    assert apres_tables == avant_tables


# --- Unitaires ------------------------------------------------------------

def test_import_roundtrip_avec_photo(admin_client, flask_app):
    """Le chemin le plus risqué en prod : les photos (LargeBinary) doivent
    survivre au dump/import (hex -> X'' ou decode('','hex'))."""
    from app import db
    from app.models import User
    photo = b'\x89PNG\r\n\x1a\n' + bytes(range(256))
    with flask_app.app_context():
        u = User.query.filter_by(email='membre@test.local').first()
        u.photo_data = photo
        db.session.commit()
    dump = admin_client.get('/settings/export-base').data
    r = admin_client.post('/api/backup/import',
                          data={'fichier': (io.BytesIO(dump), 's.sql'),
                                'confirmation': 'RESTAURER'},
                          content_type='multipart/form-data')
    assert r.status_code == 200, r.get_json()
    with flask_app.app_context():
        db.session.expire_all()
        u = User.query.filter_by(email='membre@test.local').first()
        assert bytes(u.photo_data) == photo
        u.photo_data = None
        db.session.commit()


def test_litteral_chaines_echappees():
    from app.routes_backup import _chaine, _litteral
    assert _litteral("l'agenda", String(), 'postgresql') == "'l''agenda'"
    assert _chaine('multi\nligne') == "'multi\nligne'"


def test_litteral_null_bool():
    from app.routes_backup import _litteral
    assert _litteral(None, String(), 'sqlite') == 'NULL'
    assert _litteral(True, Boolean(), 'sqlite') == 'TRUE'
    assert _litteral(False, Boolean(), 'sqlite') == 'FALSE'
    # sqlite renvoie 0/1 pour les booleennes -> rendu TRUE/FALSE via le type
    assert _litteral(1, Boolean(), 'sqlite') == 'TRUE'
    assert _litteral(0, Boolean(), 'sqlite') == 'FALSE'


def test_litteral_binaire_par_dialecte():
    from app.routes_backup import _litteral
    assert _litteral(b'\x89PNG', LargeBinary(), 'sqlite') == "X'89504e47'"
    assert _litteral(memoryview(b'\x01'), LargeBinary(), 'postgresql') == "decode('01','hex')"


def test_litteral_json_et_nombres():
    from app.routes_backup import _litteral
    assert _litteral({'a': 1}, String(), 'postgresql') == "'{\"a\": 1}'"
    assert _litteral(42, String(), 'sqlite') == '42'
    assert _litteral(None, String(), 'postgresql') == 'NULL'


def test_splitter_point_virgule_dans_chaine():
    from app.routes_backup import _splitter_statements
    sql = ("INSERT INTO a VALUES ('x;y');\n"
           "-- commentaire ; avec point-virgule\n"
           "INSERT INTO b VALUES ('l''autre; rien');")
    parts = _splitter_statements(sql)
    assert len(parts) == 2
    assert "x;y" in parts[0] and "l''autre; rien" in parts[1]


def test_ordre_topologique():
    from app.routes_backup import _ordre_topologique
    tables = ['taches', 'users', 'dossiers']
    fks = {'users': set(), 'dossiers': {'users'},
           'taches': {'users', 'dossiers'}}
    ordre = _ordre_topologique(tables, fks)
    assert ordre.index('users') < ordre.index('dossiers') < ordre.index('taches')
    assert sorted(ordre) == sorted(tables)
