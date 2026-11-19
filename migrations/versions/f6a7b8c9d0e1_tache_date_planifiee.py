"""Tache.date_planifiee : date de traitement prevue par l'assigne.

Revision ID: f6a7b8c9d0e1
Revises: e5f6a7b8c9a0
Create Date: 2026-11-19 18:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


revision = 'f6a7b8c9d0e1'
down_revision = 'e5f6a7b8c9a0'
branch_labels = None
depends_on = None


def upgrade():
    cols = [c['name'] for c in sa.inspect(op.get_bind()).get_columns('taches')]
    if 'date_planifiee' not in cols:
        op.add_column('taches', sa.Column('date_planifiee', sa.Date(), nullable=True))


def downgrade():
    cols = [c['name'] for c in sa.inspect(op.get_bind()).get_columns('taches')]
    if 'date_planifiee' in cols:
        op.drop_column('taches', 'date_planifiee')