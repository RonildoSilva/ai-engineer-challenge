-- Esquema do store.db. As decisões de docs/data_report.md viram restrições verificadas pelo banco:
-- uma linha que viole uma regra de tratamento não entra.

PRAGMA foreign_keys = ON;

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
    price_brl             REAL NOT NULL CHECK (price_brl > 0),
    stock_quantity        INTEGER NOT NULL CHECK (stock_quantity >= 0),
    status                TEXT NOT NULL CHECK (status IN ('active', 'discontinued', 'coming_soon')),
    availability          TEXT NOT NULL,
    -- descrição exposta: NULL quando não confiável (marca diverge do nome, relatório §2.3)
    description           TEXT,
    description_raw       TEXT,
    description_reliable  INTEGER NOT NULL CHECK (description_reliable IN (0, 1)),
    specs_json            TEXT NOT NULL CHECK (json_valid(specs_json)),
    created_at            TEXT NOT NULL,
    -- promoção vigente (no máximo uma; a de maior desconto entre as ativas, relatório §3)
    -- FK circular com promotions: verificada só no fim da transação de carga
    promo_id              INTEGER REFERENCES promotions(promotion_id) DEFERRABLE INITIALLY DEFERRED,
    promo_percent         INTEGER CHECK (promo_percent IS NULL OR promo_percent BETWEEN 1 AND 99),
    promo_label           TEXT,
    promo_price_brl       REAL CHECK (promo_price_brl IS NULL OR promo_price_brl < price_brl),
    -- o status prevalece sobre o estoque (relatório §2.2)
    CHECK (
        (status = 'active'       AND stock_quantity > 0 AND availability = 'disponivel')
     OR (status = 'active'       AND stock_quantity = 0 AND availability = 'esgotado')
     OR (status = 'discontinued' AND availability = 'descontinuado')
     OR (status = 'coming_soon'  AND availability = 'em_breve')
    ),
    CHECK ((description_reliable = 1) = (description IS NOT NULL)),
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
    is_active         INTEGER NOT NULL CHECK (is_active IN (0, 1))
);
CREATE INDEX idx_promotions_product ON promotions(product_id);

CREATE TABLE customers (
    customer_id   INTEGER PRIMARY KEY,
    name          TEXT NOT NULL,
    first_name    TEXT NOT NULL,
    phone         TEXT NOT NULL,
    phone_digits  TEXT NOT NULL CHECK (length(phone_digits) BETWEEN 10 AND 11),
    email         TEXT NOT NULL CHECK (email = lower(email)),
    city          TEXT NOT NULL,
    is_metro      INTEGER NOT NULL CHECK (is_metro IN (0, 1))
);
-- e-mail NÃO é único (clientes 11 e 43 compartilham): a verificação usa pedido + contato
CREATE INDEX idx_customers_email ON customers(email);
CREATE INDEX idx_customers_phone ON customers(phone_digits);

CREATE TABLE orders (
    order_id            INTEGER PRIMARY KEY,
    customer_id         INTEGER NOT NULL REFERENCES customers(customer_id),
    order_date          TEXT NOT NULL,
    status              TEXT NOT NULL
                        CHECK (status IN ('pending', 'confirmed', 'shipped', 'delivered', 'cancelled')),
    -- valor cobrado; pode divergir da soma dos itens (pedidos 3 e 20, relatório §4)
    total_brl           REAL NOT NULL CHECK (total_brl >= 0),
    payment_method      TEXT NOT NULL,
    payment_type        TEXT NOT NULL CHECK (payment_type IN ('pix', 'debito', 'credito', 'boleto')),
    installments        INTEGER NOT NULL CHECK (installments BETWEEN 1 AND 12),
    tracking_code       TEXT CHECK (tracking_code IS NULL OR tracking_code GLOB 'BR[A-Z0-9]*BR'),
    estimated_delivery  TEXT,
    notes               TEXT,
    -- só pedidos despachados têm rastreio e previsão de entrega
    CHECK ((status IN ('shipped', 'delivered')) = (tracking_code IS NOT NULL)),
    CHECK ((tracking_code IS NULL) = (estimated_delivery IS NULL)),
    CHECK (payment_type = 'credito' OR installments = 1)
);
CREATE INDEX idx_orders_customer ON orders(customer_id);

CREATE TABLE order_items (
    order_id        INTEGER NOT NULL REFERENCES orders(order_id),
    product_id      INTEGER NOT NULL REFERENCES products(product_id),
    quantity        INTEGER NOT NULL CHECK (quantity > 0),
    unit_price_brl  REAL NOT NULL CHECK (unit_price_brl > 0),  -- preço de tabela atual
    PRIMARY KEY (order_id, product_id)
);

CREATE VIEW orders_with_items AS
SELECT o.order_id, o.customer_id, o.order_date, o.status, o.total_brl,
       o.payment_type, o.installments, o.tracking_code, o.estimated_delivery, o.notes,
       i.product_id, p.name AS product_name, i.quantity, i.unit_price_brl,
       p.category_id
FROM orders o
JOIN order_items i USING (order_id)
JOIN products p USING (product_id);
