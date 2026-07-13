ALTER TABLE ml.model_task
    DROP CONSTRAINT IF EXISTS model_task_task_type_check;

ALTER TABLE ml.model_task
    ADD CONSTRAINT model_task_task_type_check
    CHECK (task_type IN ('rfm', 'kmeans', 'churn', 'analytics_refresh'));

ALTER TABLE ads.customer_rfm
    ADD COLUMN snapshot_date DATE NOT NULL DEFAULT CURRENT_DATE,
    ADD COLUMN refresh_task_id BIGINT REFERENCES ml.model_task(task_id);

CREATE INDEX idx_customer_rfm_snapshot
    ON ads.customer_rfm(snapshot_date DESC, customer_id);
CREATE INDEX idx_customer_rfm_refresh_task
    ON ads.customer_rfm(refresh_task_id)
    WHERE refresh_task_id IS NOT NULL;

CREATE TABLE ads.daily_sales (
    snapshot_date   DATE NOT NULL,
    sales_date      DATE NOT NULL,
    order_count     INTEGER NOT NULL CHECK (order_count >= 0),
    item_quantity   INTEGER NOT NULL,
    gross_amount    NUMERIC(18,2) NOT NULL CHECK (gross_amount >= 0),
    net_amount      NUMERIC(18,2) NOT NULL,
    refresh_task_id BIGINT NOT NULL REFERENCES ml.model_task(task_id),
    refreshed_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT pk_daily_sales PRIMARY KEY (snapshot_date, sales_date)
);

CREATE TABLE ads.product_sales (
    snapshot_date   DATE NOT NULL,
    product_id      BIGINT NOT NULL REFERENCES biz.product(product_id),
    category_id     BIGINT REFERENCES biz.product_category(category_id),
    quantity        INTEGER NOT NULL,
    gross_amount    NUMERIC(18,2) NOT NULL CHECK (gross_amount >= 0),
    net_amount      NUMERIC(18,2) NOT NULL,
    refresh_task_id BIGINT NOT NULL REFERENCES ml.model_task(task_id),
    refreshed_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT pk_product_sales PRIMARY KEY (snapshot_date, product_id)
);

CREATE TABLE ads.category_sales (
    snapshot_date   DATE NOT NULL,
    category_id     BIGINT REFERENCES biz.product_category(category_id),
    quantity        INTEGER NOT NULL,
    gross_amount    NUMERIC(18,2) NOT NULL CHECK (gross_amount >= 0),
    net_amount      NUMERIC(18,2) NOT NULL,
    refresh_task_id BIGINT NOT NULL REFERENCES ml.model_task(task_id),
    refreshed_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT uq_category_sales_snapshot
        UNIQUE NULLS NOT DISTINCT (snapshot_date, category_id)
);

CREATE TABLE ads.customer_profile (
    snapshot_date   DATE NOT NULL,
    customer_id     BIGINT NOT NULL REFERENCES biz.customer(customer_id),
    frequency       INTEGER NOT NULL CHECK (frequency >= 0),
    monetary        NUMERIC(18,2) NOT NULL,
    recency_days    INTEGER NOT NULL CHECK (recency_days >= 0),
    refresh_task_id BIGINT NOT NULL REFERENCES ml.model_task(task_id),
    refreshed_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT pk_customer_profile PRIMARY KEY (snapshot_date, customer_id)
);

CREATE INDEX idx_daily_sales_sales_date
    ON ads.daily_sales(sales_date DESC, snapshot_date DESC);
CREATE INDEX idx_daily_sales_refresh_task
    ON ads.daily_sales(refresh_task_id);

CREATE INDEX idx_product_sales_product_snapshot
    ON ads.product_sales(product_id, snapshot_date DESC);
CREATE INDEX idx_product_sales_category_snapshot
    ON ads.product_sales(category_id, snapshot_date DESC);
CREATE INDEX idx_product_sales_refresh_task
    ON ads.product_sales(refresh_task_id);

CREATE INDEX idx_category_sales_category_snapshot
    ON ads.category_sales(category_id, snapshot_date DESC);
CREATE INDEX idx_category_sales_refresh_task
    ON ads.category_sales(refresh_task_id);

CREATE INDEX idx_customer_profile_customer_snapshot
    ON ads.customer_profile(customer_id, snapshot_date DESC);
CREATE INDEX idx_customer_profile_refresh_task
    ON ads.customer_profile(refresh_task_id);
