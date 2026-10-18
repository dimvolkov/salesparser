"""initial

Revision ID: 0001
Revises: 
Create Date: 2026-10-18 16:05:39.324642

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '0001'
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # pg_trgm — для подсказок похожих позиций в админке
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
    op.create_table('category',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('name', sa.String(length=300), nullable=False),
    sa.Column('parent_id', sa.Integer(), nullable=True),
    sa.Column('position', sa.Integer(), nullable=False),
    sa.ForeignKeyConstraint(['parent_id'], ['category.id'], ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('source',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('name', sa.String(length=100), nullable=False),
    sa.Column('title', sa.String(length=200), nullable=False),
    sa.Column('base_url', sa.String(length=500), nullable=False),
    sa.Column('adapter', sa.String(length=200), nullable=False),
    sa.Column('config', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('is_master', sa.Boolean(), nullable=False),
    sa.Column('enabled', sa.Boolean(), nullable=False),
    sa.Column('position', sa.Integer(), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('name')
    )
    op.create_table('product',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('name', sa.String(length=500), nullable=False),
    sa.Column('brand', sa.String(length=200), nullable=True),
    sa.Column('ean', sa.String(length=14), nullable=True),
    sa.Column('sku', sa.String(length=100), nullable=True),
    sa.Column('category_id', sa.Integer(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['category_id'], ['category.id'], ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('ean'),
    sa.UniqueConstraint('sku')
    )
    op.create_index(op.f('ix_product_category_id'), 'product', ['category_id'], unique=False)
    op.create_table('scrape_run',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('source_id', sa.Integer(), nullable=False),
    sa.Column('status', sa.String(length=20), nullable=False),
    sa.Column('trigger', sa.String(length=20), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('started_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('finished_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('categories_found', sa.Integer(), nullable=False),
    sa.Column('items_found', sa.Integer(), nullable=False),
    sa.Column('errors_count', sa.Integer(), nullable=False),
    sa.Column('error_log', sa.Text(), nullable=True),
    sa.ForeignKeyConstraint(['source_id'], ['source.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_scrape_run_source_id'), 'scrape_run', ['source_id'], unique=False)
    op.create_index(op.f('ix_scrape_run_status'), 'scrape_run', ['status'], unique=False)
    op.create_table('source_category',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('source_id', sa.Integer(), nullable=False),
    sa.Column('external_id', sa.String(length=1000), nullable=False),
    sa.Column('name', sa.String(length=300), nullable=False),
    sa.Column('url', sa.String(length=2000), nullable=False),
    sa.Column('parent_id', sa.Integer(), nullable=True),
    sa.Column('position', sa.Integer(), nullable=False),
    sa.Column('category_id', sa.Integer(), nullable=True),
    sa.Column('mapping_locked', sa.Boolean(), nullable=False),
    sa.Column('last_seen_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['category_id'], ['category.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['parent_id'], ['source_category.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['source_id'], ['source.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('source_id', 'external_id')
    )
    op.create_index(op.f('ix_source_category_category_id'), 'source_category', ['category_id'], unique=False)
    op.create_index(op.f('ix_source_category_source_id'), 'source_category', ['source_id'], unique=False)
    op.create_table('source_product',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('source_id', sa.Integer(), nullable=False),
    sa.Column('external_id', sa.String(length=1000), nullable=False),
    sa.Column('url', sa.String(length=2000), nullable=False),
    sa.Column('name', sa.String(length=500), nullable=False),
    sa.Column('brand', sa.String(length=200), nullable=True),
    sa.Column('sku_raw', sa.String(length=200), nullable=True),
    sa.Column('sku', sa.String(length=100), nullable=True),
    sa.Column('ean', sa.String(length=14), nullable=True),
    sa.Column('source_category_id', sa.Integer(), nullable=True),
    sa.Column('product_id', sa.Integer(), nullable=True),
    sa.Column('match_locked', sa.Boolean(), nullable=False),
    sa.Column('first_seen_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('last_seen_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['product_id'], ['product.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['source_category_id'], ['source_category.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['source_id'], ['source.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('source_id', 'external_id')
    )
    op.create_index(op.f('ix_source_product_ean'), 'source_product', ['ean'], unique=False)
    op.create_index(op.f('ix_source_product_product_id'), 'source_product', ['product_id'], unique=False)
    op.create_index(op.f('ix_source_product_sku'), 'source_product', ['sku'], unique=False)
    op.create_index(op.f('ix_source_product_source_id'), 'source_product', ['source_id'], unique=False)
    op.create_table('price_snapshot',
    sa.Column('id', sa.BigInteger(), nullable=False),
    sa.Column('source_product_id', sa.Integer(), nullable=False),
    sa.Column('scraped_on', sa.Date(), nullable=False),
    sa.Column('price', sa.Numeric(precision=14, scale=2), nullable=True),
    sa.Column('old_price', sa.Numeric(precision=14, scale=2), nullable=True),
    sa.Column('currency', sa.String(length=3), nullable=False),
    sa.Column('in_stock', sa.Boolean(), nullable=False),
    sa.Column('scraped_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['source_product_id'], ['source_product.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('source_product_id', 'scraped_on')
    )
    op.create_index(op.f('ix_price_snapshot_scraped_on'), 'price_snapshot', ['scraped_on'], unique=False)
    # ### end Alembic commands ###


def downgrade() -> None:
    """Downgrade schema."""
    # ### commands auto generated by Alembic - please adjust! ###
    op.drop_index(op.f('ix_price_snapshot_scraped_on'), table_name='price_snapshot')
    op.drop_table('price_snapshot')
    op.drop_index(op.f('ix_source_product_source_id'), table_name='source_product')
    op.drop_index(op.f('ix_source_product_sku'), table_name='source_product')
    op.drop_index(op.f('ix_source_product_product_id'), table_name='source_product')
    op.drop_index(op.f('ix_source_product_ean'), table_name='source_product')
    op.drop_table('source_product')
    op.drop_index(op.f('ix_source_category_source_id'), table_name='source_category')
    op.drop_index(op.f('ix_source_category_category_id'), table_name='source_category')
    op.drop_table('source_category')
    op.drop_index(op.f('ix_scrape_run_status'), table_name='scrape_run')
    op.drop_index(op.f('ix_scrape_run_source_id'), table_name='scrape_run')
    op.drop_table('scrape_run')
    op.drop_index(op.f('ix_product_category_id'), table_name='product')
    op.drop_table('product')
    op.drop_table('source')
    op.drop_table('category')
    # ### end Alembic commands ###
