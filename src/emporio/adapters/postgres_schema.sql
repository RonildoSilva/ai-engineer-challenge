-- Esquema Postgres do perfil de escala. Espelha src/emporio/data/schema.sql (SQLite): as mesmas
-- decisões de tratamento viram as mesmas restrições, agora com tipos nativos (DATE, JSON,
-- BOOLEAN, vector). Carregado por `emporio db load` dentro de uma transação, no schema atual
-- (search_path), de forma idempotente.

CREATE EXTENSION IF NOT EXISTS vector;

DROP TABLE IF EXISTS order_items, orders, customers, promotions, products, categories, meta,
                     vectors, vectors_meta CASCADE;

CREATE TABLE meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE categories (
    category_id  INTEGER PRIMARY KEY,
    name         TEXT NOT NULL UNIQUE,
    description  TEXT
);

CREATE TABLE products (
    product_id            INTEGER PRIMARY KEY,
    name                  TEXT NOT NULL UNIQUE,
    name_normalized       TEXT NOT NULL,
    search_text           TEXT NOT NULL,
    category_id           INTEGER NOT NULL REFERENCES categories(category_id),
    price_brl             DOUBLE PRECISION NOT NULL CHECK (price_brl > 0),
    stock_quantity        INTEGER NOT NULL CHECK (stock_quantity >= 0),
    status                TEXT NOT NULL CHECK (status IN ('active', 'discontinued', 'coming_soon')),
    availability          TEXT NOT NULL,
    description           TEXT,
    description_raw       TEXT,
    description_reliable  BOOLEAN NOT NULL,
    -- JSON (não JSONB): preserva a ordem das chaves; com JSONB o texto dos documentos de busca
    -- mudava e o cache de vetores (por hash do texto) deixava de valer entre os backends
    specs                 JSON NOT NULL,
    created_at            DATE NOT NULL,
    promo_id              INTEGER,
    promo_percent         INTEGER CHECK (promo_percent IS NULL OR promo_percent BETWEEN 1 AND 99),
    promo_label           TEXT,
    promo_price_brl       DOUBLE PRECISION CHECK (promo_price_brl IS NULL OR promo_price_brl < price_brl),
    CHECK (
        (status = 'active'       AND stock_quantity > 0 AND availability = 'disponivel')
     OR (status = 'active'       AND stock_quantity = 0 AND availability = 'esgotado')
     OR (status = 'discontinued' AND availability = 'descontinuado')
     OR (status = 'coming_soon'  AND availability = 'em_breve')
    ),
    CHECK (description_reliable = (description IS NOT NULL)),
    CHECK ((promo_id IS NULL) = (promo_percent IS NULL)
       AND (promo_id IS NULL) = (promo_price_brl IS NULL))
);
CREATE INDEX idx_products_category ON products(category_id, price_brl);
CREATE INDEX idx_products_availability ON products(availability);

CREATE TABLE promotions (
    promotion_id      INTEGER PRIMARY KEY,
    product_id        INTEGER NOT NULL REFERENCES products(product_id),
    discount_percent  INTEGER NOT NULL CHECK (discount_percent BETWEEN 1 AND 99),
    description       TEXT NOT NULL,
    is_active         BOOLEAN NOT NULL
);
-- FK circular (produto → promoção vigente): criada depois que as duas tabelas existem e
-- verificada só no fim da transação de carga
ALTER TABLE products ADD CONSTRAINT fk_products_promo FOREIGN KEY (promo_id)
    REFERENCES promotions(promotion_id) DEFERRABLE INITIALLY DEFERRED;

CREATE TABLE customers (
    customer_id   INTEGER PRIMARY KEY,
    name          TEXT NOT NULL,
    first_name    TEXT NOT NULL,
    phone         TEXT NOT NULL,
    phone_digits  TEXT NOT NULL CHECK (length(phone_digits) BETWEEN 10 AND 11),
    email         TEXT NOT NULL CHECK (email = lower(email)),
    city          TEXT NOT NULL,
    is_metro      BOOLEAN NOT NULL
);
CREATE INDEX idx_customers_email ON customers(email);
CREATE INDEX idx_customers_phone ON customers(phone_digits);

CREATE TABLE orders (
    order_id            INTEGER PRIMARY KEY,
    customer_id         INTEGER NOT NULL REFERENCES customers(customer_id),
    order_date          DATE NOT NULL,
    status              TEXT NOT NULL
                        CHECK (status IN ('pending', 'confirmed', 'shipped', 'delivered', 'cancelled')),
    total_brl           DOUBLE PRECISION NOT NULL CHECK (total_brl >= 0),
    payment_method      TEXT NOT NULL,
    payment_type        TEXT NOT NULL CHECK (payment_type IN ('pix', 'debito', 'credito', 'boleto')),
    installments        INTEGER NOT NULL CHECK (installments BETWEEN 1 AND 12),
    tracking_code       TEXT CHECK (tracking_code IS NULL OR tracking_code ~ '^BR[A-Z0-9]*BR$'),
    estimated_delivery  DATE,
    notes               TEXT,
    CHECK ((status IN ('shipped', 'delivered')) = (tracking_code IS NOT NULL)),
    CHECK ((tracking_code IS NULL) = (estimated_delivery IS NULL)),
    CHECK (payment_type = 'credito' OR installments = 1)
);
CREATE INDEX idx_orders_customer ON orders(customer_id);

CREATE TABLE order_items (
    order_id        INTEGER NOT NULL REFERENCES orders(order_id),
    product_id      INTEGER NOT NULL REFERENCES products(product_id),
    quantity        INTEGER NOT NULL CHECK (quantity > 0),
    unit_price_brl  DOUBLE PRECISION NOT NULL CHECK (unit_price_brl > 0),
    PRIMARY KEY (order_id, product_id)
);

-- Índice vetorial (pgvector). A dimensão vem do modelo de embeddings (768 no embeddinggemma);
-- a coluna é criada sem dimensão fixa e o índice HNSW, na carga, com a dimensão dos dados.
CREATE TABLE vectors_meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE vectors (
    doc_id     TEXT PRIMARY KEY,
    kind       TEXT NOT NULL CHECK (kind IN ('product', 'policy')),
    title      TEXT NOT NULL,
    text_hash  TEXT NOT NULL,
    vec        vector NOT NULL
);
