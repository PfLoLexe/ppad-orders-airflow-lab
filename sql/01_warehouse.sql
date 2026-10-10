-- Учебное хранилище заказов. Таблицы одной предметной области.
-- Один набор генератора на экземпляр warehouse; смена набора требует
-- отдельного хранилища или явно согласованного сброса учебных данных.
CREATE SCHEMA IF NOT EXISTS ops;
CREATE SCHEMA IF NOT EXISTS stg;
CREATE SCHEMA IF NOT EXISTS dds;
CREATE SCHEMA IF NOT EXISTS cdm;

CREATE TABLE IF NOT EXISTS ops.dataset (
    singleton boolean PRIMARY KEY DEFAULT true CHECK (singleton),
    dataset_id text NOT NULL UNIQUE,
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS ops.batches (
    snapshot_date date PRIMARY KEY,
    dataset_id text NOT NULL REFERENCES ops.dataset(dataset_id),
    source_sha256 char(64) NOT NULL,
    stg_loaded_at timestamptz,
    dds_loaded_at timestamptz,
    cdm_loaded_at timestamptz
);

-- STG хранит исходный снимок целиком, без ограничений на бизнес-поля JSON.
CREATE TABLE IF NOT EXISTS stg.snapshots (
    snapshot_date date PRIMARY KEY,
    dataset_id text NOT NULL,
    payload jsonb NOT NULL,
    source_sha256 char(64) NOT NULL,
    loaded_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS dds.cities (
    city_id smallint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    city_name text NOT NULL UNIQUE
);
CREATE TABLE IF NOT EXISTS dds.customers (
    customer_id integer PRIMARY KEY,
    last_name text NOT NULL,
    first_name text NOT NULL,
    patronymic text NOT NULL,
    city_id smallint NOT NULL REFERENCES dds.cities(city_id)
);
CREATE TABLE IF NOT EXISTS dds.categories (
    category_id integer PRIMARY KEY,
    category_name text NOT NULL UNIQUE
);
CREATE TABLE IF NOT EXISTS dds.products (
    product_id integer PRIMARY KEY
);
CREATE TABLE IF NOT EXISTS dds.product_versions (
    product_version_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    product_id integer NOT NULL REFERENCES dds.products(product_id),
    product_name text NOT NULL,
    category_id integer NOT NULL REFERENCES dds.categories(category_id),
    catalog_price numeric(12,2) NOT NULL CHECK (catalog_price > 0),
    valid_from timestamptz NOT NULL,
    valid_to timestamptz,
    UNIQUE (product_id, valid_from),
    CHECK (valid_to IS NULL OR valid_to > valid_from)
);
CREATE UNIQUE INDEX IF NOT EXISTS one_current_product_version
    ON dds.product_versions(product_id) WHERE valid_to IS NULL;
CREATE INDEX IF NOT EXISTS product_version_intervals
    ON dds.product_versions(product_id, valid_from, valid_to);
CREATE OR REPLACE VIEW dds.current_products AS
    SELECT *, true AS is_current FROM dds.product_versions WHERE valid_to IS NULL;

CREATE TABLE IF NOT EXISTS dds.statuses (
    status_code text PRIMARY KEY,
    status_name text NOT NULL UNIQUE
);
INSERT INTO dds.statuses VALUES
    ('created', 'Создан'), ('paid', 'Оплачен'), ('shipped', 'Отправлен'),
    ('delivered', 'Доставлен'), ('cancelled', 'Отменён')
ON CONFLICT (status_code) DO NOTHING;
CREATE TABLE IF NOT EXISTS dds.orders (
    order_id integer PRIMARY KEY,
    customer_id integer NOT NULL REFERENCES dds.customers(customer_id),
    created_at timestamptz NOT NULL
);
CREATE TABLE IF NOT EXISTS dds.order_status_versions (
    order_status_version_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    order_id integer NOT NULL REFERENCES dds.orders(order_id),
    status_code text NOT NULL REFERENCES dds.statuses(status_code),
    valid_from timestamptz NOT NULL,
    valid_to timestamptz,
    UNIQUE (order_id, valid_from),
    CHECK (valid_to IS NULL OR valid_to > valid_from)
);
CREATE UNIQUE INDEX IF NOT EXISTS one_current_order_status
    ON dds.order_status_versions(order_id) WHERE valid_to IS NULL;
CREATE TABLE IF NOT EXISTS dds.order_items (
    order_id integer NOT NULL REFERENCES dds.orders(order_id),
    line_no smallint NOT NULL CHECK (line_no > 0),
    product_version_id bigint NOT NULL REFERENCES dds.product_versions(product_version_id),
    quantity integer NOT NULL CHECK (quantity > 0),
    unit_price numeric(12,2) NOT NULL CHECK (unit_price > 0),
    PRIMARY KEY (order_id, line_no)
);

-- Одна строка на заказ в каждом наблюдаемом снимке; суммы считаются
-- отдельно от позиций, чтобы средний чек не взвешивался числом позиций.
CREATE TABLE IF NOT EXISTS cdm.order_snapshots (
    snapshot_date date NOT NULL,
    order_id integer NOT NULL,
    created_at timestamptz NOT NULL,
    created_date date NOT NULL,
    delivered_at timestamptz,
    delivered_date date,
    customer_id integer NOT NULL,
    customer_full_name text NOT NULL,
    city_name text NOT NULL,
    status_code text NOT NULL,
    status_name text NOT NULL,
    order_amount numeric(14,2) NOT NULL,
    delivery_hours numeric(14,4),
    PRIMARY KEY (snapshot_date, order_id)
);
CREATE TABLE IF NOT EXISTS cdm.order_item_snapshots (
    snapshot_date date NOT NULL,
    order_id integer NOT NULL,
    line_no smallint NOT NULL,
    product_id integer NOT NULL,
    product_name text NOT NULL,
    category_id integer NOT NULL,
    category_name text NOT NULL,
    quantity integer NOT NULL,
    unit_price numeric(12,2) NOT NULL,
    line_amount numeric(14,2) NOT NULL,
    PRIMARY KEY (snapshot_date, order_id, line_no)
);
CREATE INDEX IF NOT EXISTS cdm_order_dates
    ON cdm.order_snapshots(snapshot_date, created_date, delivered_date);
CREATE INDEX IF NOT EXISTS cdm_item_categories
    ON cdm.order_item_snapshots(snapshot_date, category_id, order_id);

CREATE TABLE IF NOT EXISTS cdm.daily_orders (
    snapshot_date date NOT NULL,
    created_date date NOT NULL,
    city_name text NOT NULL,
    orders_count integer NOT NULL,
    ordered_amount numeric(16,2) NOT NULL,
    cancelled_count integer NOT NULL,
    PRIMARY KEY (snapshot_date, created_date, city_name)
);
CREATE TABLE IF NOT EXISTS cdm.daily_sales (
    snapshot_date date NOT NULL,
    delivered_date date NOT NULL,
    city_name text NOT NULL,
    category_id integer NOT NULL,
    category_name text NOT NULL,
    units_sold integer NOT NULL,
    sales_amount numeric(16,2) NOT NULL,
    PRIMARY KEY (snapshot_date, delivered_date, city_name, category_id)
);
