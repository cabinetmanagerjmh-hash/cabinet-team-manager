"""Sauvegarde / restauration de la base (admin).

Contexte : saturation de l'allowance Neon (transfert reseau) -> la base peut
etre coupee. Solution : exporter les donnees en .sql depuis l'app, creer un
nouveau projet Neon, changer DATABASE_URL sur Render, puis reimporter ici.

- Export  : dump DATA-ONLY via l'inspector (couvre toutes les tables, y compris
  celles creees en SQL brut). Pas de schema : release_migrate.py le recree au
  demarrage sur la nouvelle base. alembic_version exclu.
- Import  : Postgres -> FK internes retirees puis recreees a l'identique dans la
  meme transaction (le cycle users/equipes interdit tout ordre DELETE/INSERT) ;
  SQLite -> suppression en ordre inverse des FKs. Confirmation « RESTAURER ».
- Limite d'upload : relevee le temps de la requete d'import (le dump contient
  les photos en binaire et peut depasser MAX_CONTENT_LENGTH global de 16 Mo).
"""
import json
import re
from datetime import datetime
from decimal import Decimal

from flask import Response, current_app, g, jsonify, redirect, request, url_for
from flask_login import current_user, login_required
from sqlalchemy.sql import sqltypes

from . import app, db

_CONFIRMATION = 'RESTAURER'
_UPLOAD_LIMIT = 512 * 1024 * 1024
_ROWS_PAR_LOT = 100
_LOT_MAX_OCTETS = 1_000_000


# ---------------------------------------------------------------------------
# Helpers : rendu des litteraux + decoupage des instructions
# ---------------------------------------------------------------------------

def _chaine(s):
    return "'" + str(s).replace("'", "''") + "'"


def _litteral(valeur, coltype, dialect):
    """Rend une valeur Python en litteral SQL supporte par sqlite ET postgres."""
    if valeur is None:
        return 'NULL'
    if isinstance(valeur, bool):
        return 'TRUE' if valeur else 'FALSE'
    if isinstance(coltype, sqltypes.Boolean) and isinstance(valeur, int) and valeur in (0, 1):
        return 'TRUE' if valeur else 'FALSE'
    if isinstance(valeur, (dict, list)):
        return _chaine(json.dumps(valeur))
    if isinstance(valeur, (bytes, memoryview)):
        hexa = bytes(valeur).hex()
        if dialect == 'sqlite':
            return f"X'{hexa}'"
        return f"decode('{hexa}','hex')"
    if isinstance(valeur, Decimal):
        return str(valeur)
    if isinstance(valeur, float):
        return repr(valeur)
    if isinstance(valeur, int):
        return str(valeur)
    return _chaine(valeur)


def _splitter_statements(sql):
    """Decoupe sur « ; » hors chaines, en ignorant les commentaires « -- ».

    Gerre l'echappement « '' » : un point-virgule dans un libelle ne coupe pas.
    """
    stmts, buf, in_str = [], [], False
    i, n = 0, len(sql)
    while i < n:
        c = sql[i]
        if in_str:
            buf.append(c)
            if c == "'":
                if i + 1 < n and sql[i + 1] == "'":
                    buf.append("'")
                    i += 2
                    continue
                in_str = False
            i += 1
            continue
        if c == "'":
            in_str = True
            buf.append(c)
            i += 1
            continue
        if c == '-' and sql[i:i + 2] == '--':
            j = sql.find('\n', i)
            i = n if j == -1 else j
            continue
        if c == ';':
            s = ''.join(buf).strip()
            if s:
                stmts.append(s)
            buf = []
            i += 1
            continue
        buf.append(c)
        i += 1
    tail = ''.join(buf).strip()
    if tail:
        stmts.append(tail)
    return stmts


def _ordre_topologique(tables, fks):
    """Tables referencees (dependencies) d'abord. En cas de cycle, la table
    est inseree quand meme (garde-fou, ne doit pas arriver sur ce schema)."""
    visite, en_cours, ordre = set(), set(), []

    def voir(t):
        if t in visite or t in en_cours:
            return
        en_cours.add(t)
        for dep in sorted(fks.get(t, ())):
            voir(dep)
        en_cours.discard(t)
        visite.add(t)
        ordre.append(t)

    for t in tables:
        voir(t)
    return ordre


def _cartographie():
    """Tables (hors alembic_version) + colonnes + FKs internes, via l'inspector."""
    insp = db.inspect(db.engine)
    tables = [t for t in insp.get_table_names() if t != 'alembic_version']
    fks, colonnes = {}, {}
    for t in tables:
        colonnes[t] = {c['name']: c for c in insp.get_columns(t)}
        refs = set()
        for fk in insp.get_foreign_keys(t):
            rt = fk.get('referred_table')
            if rt and rt in tables and rt != t:
                refs.add(rt)
        fks[t] = refs
    return tables, colonnes, fks


# ---------------------------------------------------------------------------
# Export
# ---------------------------------------------------------------------------

def _construire_dump():
    dialect = db.engine.dialect.name
    tables, colonnes, fks = _cartographie()
    ordre = _ordre_topologique(tables, fks)
    lignes_sql = []
    total_lignes = 0
    with db.engine.connect() as conn:
        for t in ordre:
            noms = list(colonnes[t].keys())
            collist = ', '.join(f'"{c}"' for c in noms)
            res = conn.execute(db.text(f'SELECT * FROM "{t}"'))
            lots, lot, taille = [], [], 0
            for row in res:
                vals = ', '.join(
                    _litteral(row._mapping[nom], colonnes[t][nom]['type'], dialect)
                    for nom in noms
                )
                tup = f'({vals})'
                lot.append(tup)
                taille += len(tup)
                if len(lot) >= _ROWS_PAR_LOT or taille > _LOT_MAX_OCTETS:
                    lots.append(lot)
                    lot, taille = [], 0
            if lot:
                lots.append(lot)
            for l in lots:
                lignes_sql.append(
                    f'INSERT INTO "{t}" ({collist}) VALUES\n  ' + ',\n  '.join(l) + ';'
                )
            total_lignes += sum(len(l) for l in lots)
    entete = (
        f"-- JMH Orbit backup v1\n"
        f"-- Genere le {datetime.now().strftime('%Y-%m-%d %H:%M')} ({dialect})\n"
        f"-- {total_lignes} lignes / {len(tables)} tables. Donnees uniquement :\n"
        f"-- le schema est recree par scripts/release_migrate.py au demarrage.\n"
    )
    return entete + '\n'.join(lignes_sql) + ('\n' if lignes_sql else '')


# ---------------------------------------------------------------------------
# Import
# ---------------------------------------------------------------------------

def _executer_import(contenu_sql):
    dialect = db.engine.dialect.name
    tables, _, fks = _cartographie()
    ordre = _ordre_topologique(tables, fks)
    stmts = _splitter_statements(contenu_sql)
    lignes_inseres = 0
    engine = db.engine
    if dialect == 'postgresql':
        # Le pooler Neon (pgbouncer, mode transaction) coupe les longues
        # transactions bulk DDL+DML (« SSL connection closed unexpectedly »).
        # L'import passe par l'endpoint DIRECT (hote sans « -pooler »), prevu
        # pour les migrations et chargements massifs.
        url = db.engine.url.render_as_string(hide_password=False)
        if '-pooler.' in url:
            from sqlalchemy import create_engine
            engine = create_engine(url.replace('-pooler.', '.', 1))
    with engine.begin() as conn:
        fk_defs = []
        if dialect == 'postgresql':
            # Le schema contient un cycle (users.equipe_id <-> equipes.manager_id)
            # et une auto-FK (taches.template_id) : aucun ordre DELETE/INSERT ne
            # satisfait les FK immediates de Postgres, et le dump peut inserer
            # equipes avant users. On retire toutes les FK internes le temps de
            # l'import puis on les recree a l'identique — dans LA MEME transaction :
            # le moindre echec restaure aussi les contraintes (rollback).
            insp = db.inspect(engine)
            for t in tables:
                for fk in insp.get_foreign_keys(t):
                    rt = fk.get('referred_table')
                    if rt and rt in tables and fk.get('name'):
                        fk_defs.append((t, fk))
            for t, fk in fk_defs:
                conn.execute(db.text(f'ALTER TABLE "{t}" DROP CONSTRAINT "{fk["name"]}"'))
            for t in tables:
                conn.execute(db.text(f'DELETE FROM "{t}"'))
        else:
            # SQLite : FK non appliquees par defaut, l'ordre inverse suffit.
            for t in reversed(ordre):
                conn.execute(db.text(f'DELETE FROM "{t}"'))
        for s in stmts:
            haut = s.lstrip().upper()
            if dialect == 'sqlite' and haut.startswith('SELECT SETVAL'):
                continue
            if dialect == 'sqlite':
                s = re.sub(r"decode\('([0-9A-Fa-f]*)','hex'\)", r"X'\1'", s)
            elif dialect == 'postgresql':
                s = re.sub(r"X'([0-9A-Fa-f]*)'", r"decode('\1','hex')", s)
            res = conn.execute(db.text(s))
            if haut.startswith('INSERT') and res.rowcount and res.rowcount > 0:
                lignes_inseres += res.rowcount
        if dialect == 'postgresql':
            for t, fk in fk_defs:
                cols = ', '.join(f'"{c}"' for c in fk['constrained_columns'])
                rcols = ', '.join(f'"{c}"' for c in fk['referred_columns'])
                conn.execute(db.text(
                    f'ALTER TABLE "{t}" ADD CONSTRAINT "{fk["name"]}" '
                    f'FOREIGN KEY ({cols}) REFERENCES "{fk["referred_table"]}" ({rcols})'
                ))
            # Resynchronise les sequences sur le MAX reel des donnees.
            for t in tables:
                for c in db.inspect(engine).get_columns(t):
                    if 'nextval' in str(c.get('default') or '') and isinstance(c['type'], sqltypes.Integer):
                        mx = conn.execute(
                            db.text(f'SELECT COALESCE(MAX("{c["name"]}"), 0) FROM "{t}"')
                        ).scalar() or 0
                        conn.execute(
                            db.text('SELECT setval(pg_get_serial_sequence(:tbl, :col), :mx, :appel)'),
                            {'tbl': t, 'col': c['name'],
                             'mx': mx if mx else 1, 'appel': bool(mx)},
                        )
    if engine is not db.engine:
        engine.dispose()
    return {'tables': len(tables), 'lignes': lignes_inseres}


# ---------------------------------------------------------------------------
# Limite d'upload dynamique (le dump contient les photos en binaire)
# ---------------------------------------------------------------------------

def _backup_big_upload():
    # Enregistree en TETE de file (insert(0)) : doit tourner AVANT la validation
    # CSRF, qui lit request.form et declenche donc le parse du corps avec la
    # limite en vigueur a ce moment.
    if request.endpoint == 'backup_import':
        g._mcl_avant = app.config.get('MAX_CONTENT_LENGTH')
        app.config['MAX_CONTENT_LENGTH'] = _UPLOAD_LIMIT


@app.teardown_request
def _backup_restore_limit(exc=None):
    try:
        old = g.pop('_mcl_avant', None)
    except RuntimeError:
        old = None
    if old is not None:
        app.config['MAX_CONTENT_LENGTH'] = old


app.before_request_funcs[None].insert(0, _backup_big_upload)


# ---------------------------------------------------------------------------
# Routes (admin uniquement)
# ---------------------------------------------------------------------------

@app.route('/api/backup/stats')
@login_required
def backup_stats():
    if current_user.role != 'admin':
        return jsonify({'ok': False, 'message': 'Accès refusé.'}), 403
    tables, _, _ = _cartographie()
    stats, total = [], 0
    with db.engine.connect() as conn:
        for t in sorted(tables):
            n = conn.execute(db.text(f'SELECT COUNT(*) FROM "{t}"')).scalar() or 0
            total += n
            stats.append({'table': t, 'lignes': n})
    return jsonify({'ok': True, 'backend': db.engine.dialect.name,
                    'total': total, 'tables': stats})


@app.route('/settings/export-base')
@login_required
def backup_export():
    if current_user.role != 'admin':
        return redirect(url_for('profil'))
    dump = _construire_dump()
    nom = f"jmh-orbit-sauvegarde-{datetime.now().strftime('%Y%m%d-%H%M')}.sql"
    return Response(dump, mimetype='application/sql',
                    headers={'Content-Disposition': f'attachment; filename={nom}'})


@app.route('/api/backup/import', methods=['POST'])
@login_required
def backup_import():
    if current_user.role != 'admin':
        return jsonify({'ok': False, 'message': 'Accès refusé.'}), 403
    try:
        app.config['MAX_CONTENT_LENGTH'] = _UPLOAD_LIMIT
        if (request.form.get('confirmation') or '').strip() != _CONFIRMATION:
            return jsonify({'ok': False,
                            'message': f"Confirmation erronée : tapez exactement {_CONFIRMATION}."}), 400
        f = request.files.get('fichier')
        if f is None or not f.filename:
            return jsonify({'ok': False, 'message': 'Aucun fichier reçu.'}), 400
        try:
            contenu = f.read().decode('utf-8-sig')
        except UnicodeDecodeError:
            return jsonify({'ok': False, 'message': 'Fichier illisible (UTF-8 attendu).'}), 400
        if '-- JMH Orbit backup v1' not in contenu:
            return jsonify({'ok': False,
                            'message': "Ce fichier n'est pas une sauvegarde JMH Orbit."}), 400
    finally:
        old = g.pop('_mcl_avant', None)
        if old is not None:
            app.config['MAX_CONTENT_LENGTH'] = old
    try:
        res = _executer_import(contenu)
    except Exception as e:
        current_app.logger.warning(f"[BACKUP] import KO : {e}")
        return jsonify({'ok': False,
                        'message': "Import échoué (rien n'a été modifié) : " + str(e)[:200]}), 400
    return jsonify({'ok': True,
                    'message': f"Base restaurée : {res['lignes']} lignes réinsérées "
                               f"sur {res['tables']} tables."})
