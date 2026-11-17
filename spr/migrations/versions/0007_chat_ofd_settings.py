"""chat ofd settings

Revision ID: 0007
Revises: 0006
Create Date: 2026-11-17 12:00:00.000000
"""
from alembic import op
import sqlalchemy as sa


revision = '0007'
down_revision = '0006'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('chats', sa.Column('ofd_enabled', sa.Boolean(), server_default=sa.text('true'), nullable=False))
    op.add_column('chats', sa.Column('ofd_privacy', sa.Boolean(), server_default=sa.text('false'), nullable=False))


def downgrade():
    op.drop_column('chats', 'ofd_privacy')
    op.drop_column('chats', 'ofd_enabled')
