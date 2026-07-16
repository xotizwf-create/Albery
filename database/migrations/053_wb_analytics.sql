-- 053_wb_analytics.sql
-- Idempotent. WB seller analytics store (вкладка «WB-кабинет»: Общий дашборд, РНП, ОПиУ, ДДС,
-- По артикулам, Налоговый калькулятор). Design rules: every fact row keeps the FULL raw API
-- payload in raw jsonb (API changes never lose data; new/derived metrics can be added later
-- without re-fetching), natural unique keys for idempotent upserts, brand denormalized onto
-- facts for cheap brand filters. UI/agent read ONLY these tables — never the WB API directly
-- (the per-seller global rate limiter 429s on the second quick call).

CREATE TABLE IF NOT EXISTS wb_cards (
    nm_id        bigint PRIMARY KEY,
    imt_id       bigint,
    vendor_code  text,
    brand        text,
    title        text,
    subject_id   bigint,
    subject_name text,
    photo_url    text,
    raw          jsonb,
    updated_at   timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS wb_cards_brand_idx ON wb_cards (brand);

CREATE TABLE IF NOT EXISTS wb_orders (
    id               bigserial PRIMARY KEY,
    srid             text UNIQUE NOT NULL,
    g_number         text,
    date             timestamptz,
    last_change_date timestamptz,
    nm_id            bigint,
    barcode          text,
    supplier_article text,
    tech_size        text,
    brand            text,
    subject          text,
    category         text,
    warehouse_name   text,
    warehouse_type   text,
    region_name      text,
    oblast           text,
    country          text,
    income_id        bigint,
    is_cancel        boolean DEFAULT false,
    cancel_date      timestamptz,
    total_price      numeric,
    discount_percent numeric,
    spp              numeric,
    finished_price   numeric,
    price_with_disc  numeric,
    order_type       text,
    sticker          text,
    raw              jsonb,
    synced_at        timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS wb_orders_date_idx ON wb_orders (date);
CREATE INDEX IF NOT EXISTS wb_orders_nm_date_idx ON wb_orders (nm_id, date);
CREATE INDEX IF NOT EXISTS wb_orders_brand_date_idx ON wb_orders (brand, date);
CREATE INDEX IF NOT EXISTS wb_orders_lcd_idx ON wb_orders (last_change_date);

CREATE TABLE IF NOT EXISTS wb_sales (
    id               bigserial PRIMARY KEY,
    sale_id          text UNIQUE NOT NULL,
    srid             text,
    g_number         text,
    date             timestamptz,
    last_change_date timestamptz,
    nm_id            bigint,
    barcode          text,
    supplier_article text,
    tech_size        text,
    brand            text,
    subject          text,
    category         text,
    warehouse_name   text,
    region_name      text,
    oblast           text,
    country          text,
    is_return        boolean DEFAULT false,   -- sale_id префикс R = возврат
    total_price      numeric,
    discount_percent numeric,
    spp              numeric,
    for_pay          numeric,
    finished_price   numeric,
    price_with_disc  numeric,
    payment_sale_amount numeric,
    order_type       text,
    raw              jsonb,
    synced_at        timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS wb_sales_date_idx ON wb_sales (date);
CREATE INDEX IF NOT EXISTS wb_sales_nm_date_idx ON wb_sales (nm_id, date);
CREATE INDEX IF NOT EXISTS wb_sales_brand_date_idx ON wb_sales (brand, date);
CREATE INDEX IF NOT EXISTS wb_sales_lcd_idx ON wb_sales (last_change_date);

CREATE TABLE IF NOT EXISTS wb_stocks_daily (
    snapshot_date   date NOT NULL,
    nm_id           bigint NOT NULL,
    barcode         text NOT NULL,
    warehouse_name  text NOT NULL,
    supplier_article text,
    brand           text,
    subject         text,
    tech_size       text,
    quantity        integer,
    in_way_to_client integer,
    in_way_from_client integer,
    quantity_full   integer,
    price           numeric,
    discount        numeric,
    raw             jsonb,
    PRIMARY KEY (snapshot_date, nm_id, barcode, warehouse_name)
);
CREATE INDEX IF NOT EXISTS wb_stocks_daily_nm_idx ON wb_stocks_daily (nm_id, snapshot_date);
CREATE INDEX IF NOT EXISTS wb_stocks_daily_brand_idx ON wb_stocks_daily (brand, snapshot_date);

CREATE TABLE IF NOT EXISTS wb_finance_details (
    rrd_id            bigint PRIMARY KEY,
    realizationreport_id bigint,
    date_from         date,
    date_to           date,
    create_dt         date,
    rr_dt             date,
    nm_id             bigint,
    brand_name        text,
    subject_name      text,
    sa_name           text,
    ts_name           text,
    barcode           text,
    doc_type_name     text,
    supplier_oper_name text,
    office_name       text,
    order_dt          timestamptz,
    sale_dt           timestamptz,
    quantity          numeric,
    retail_price      numeric,
    retail_amount     numeric,
    retail_price_withdisc_rub numeric,
    ppvz_for_pay      numeric,
    delivery_rub      numeric,
    delivery_amount   numeric,
    return_amount     numeric,
    storage_fee       numeric,
    penalty           numeric,
    deduction         numeric,
    acquiring_fee     numeric,
    acquiring_percent numeric,
    ppvz_sales_commission numeric,
    commission_percent numeric,
    ppvz_vw           numeric,
    ppvz_vw_nds       numeric,
    rebill_logistic_cost numeric,
    raw               jsonb,
    synced_at         timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS wb_fin_rrdt_idx ON wb_finance_details (rr_dt);
CREATE INDEX IF NOT EXISTS wb_fin_nm_idx ON wb_finance_details (nm_id, rr_dt);
CREATE INDEX IF NOT EXISTS wb_fin_brand_idx ON wb_finance_details (brand_name, rr_dt);
CREATE INDEX IF NOT EXISTS wb_fin_report_idx ON wb_finance_details (realizationreport_id);
CREATE INDEX IF NOT EXISTS wb_fin_oper_idx ON wb_finance_details (supplier_oper_name);

CREATE TABLE IF NOT EXISTS wb_adv_costs (
    id          bigserial PRIMARY KEY,
    upd_num     bigint,
    upd_time    timestamptz,
    upd_sum     numeric,
    advert_id   bigint,
    campaign_name text,
    advert_type integer,
    payment_type text,
    raw         jsonb,
    synced_at   timestamptz NOT NULL DEFAULT now(),
    UNIQUE (upd_num, advert_id)
);
CREATE INDEX IF NOT EXISTS wb_adv_costs_time_idx ON wb_adv_costs (upd_time);

CREATE TABLE IF NOT EXISTS wb_paid_storage (
    date          date NOT NULL,
    nm_id         bigint NOT NULL,
    barcode       text NOT NULL DEFAULT '',
    warehouse     text NOT NULL DEFAULT '',
    brand         text,
    vendor_code   text,
    volume        numeric,
    warehouse_coef numeric,
    calc_type     text,
    amount        numeric,
    raw           jsonb,
    PRIMARY KEY (date, nm_id, barcode, warehouse)
);
CREATE INDEX IF NOT EXISTS wb_paid_storage_nm_idx ON wb_paid_storage (nm_id, date);

CREATE TABLE IF NOT EXISTS wb_prices_current (
    snapshot_date date NOT NULL,
    nm_id         bigint NOT NULL,
    size_id       bigint NOT NULL DEFAULT 0,
    price         numeric,
    discounted_price numeric,
    discount      numeric,
    club_discount numeric,
    raw           jsonb,
    PRIMARY KEY (snapshot_date, nm_id, size_id)
);

-- Себестоимость (вносится командой; Excel-шаблон импортируется позже — пока болванка)
CREATE TABLE IF NOT EXISTS wb_cost_prices (
    barcode     text PRIMARY KEY,
    nm_id       bigint,
    vendor_code text,
    cost        numeric NOT NULL,
    valid_from  date NOT NULL DEFAULT CURRENT_DATE,
    source      text,           -- имя файла/кто внёс
    updated_at  timestamptz NOT NULL DEFAULT now()
);

-- Налоговые режимы (несколько строк — история; активная = максимальная effective_from <= today)
CREATE TABLE IF NOT EXISTS wb_tax_settings (
    id            bigserial PRIMARY KEY,
    mode          text NOT NULL,          -- usn_d | usn_dr | ausn_d | ausn_dr | sng
    rate          numeric NOT NULL,       -- ставка налога, %
    vat_mode      text NOT NULL DEFAULT 'none',  -- none | vat5 | vat7 | vat20
    vat_refund_percent numeric NOT NULL DEFAULT 0,
    effective_from date NOT NULL DEFAULT CURRENT_DATE,
    created_at    timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS wb_sync_state (
    endpoint    text PRIMARY KEY,
    last_from   timestamptz,
    last_rrd_id bigint,
    last_run_at timestamptz,
    status      text,
    note        text
);

CREATE TABLE IF NOT EXISTS wb_sync_log (
    id         bigserial PRIMARY KEY,
    endpoint   text NOT NULL,
    started_at timestamptz NOT NULL DEFAULT now(),
    finished_at timestamptz,
    rows_upserted integer,
    ok         boolean,
    error      text
);

-- РНП/дашборд: расширяемый слой производных метрик — VIEW поверх фактов. Новые метрики =
-- правка VIEW (или новые VIEW), без миграций данных. Агент читает эти же VIEW.
CREATE OR REPLACE VIEW wb_daily_metrics AS
SELECT d.day::date AS day,
       o.brand,
       COALESCE(o.orders_cnt, 0)  AS orders_cnt,
       COALESCE(o.orders_rub, 0)  AS orders_rub,
       COALESCE(s.sales_cnt, 0)   AS sales_cnt,
       COALESCE(s.sales_rub, 0)   AS sales_rub,
       COALESCE(s.returns_cnt, 0) AS returns_cnt,
       COALESCE(s.returns_rub, 0) AS returns_rub,
       COALESCE(st.stock_qty, 0)  AS stock_qty
FROM (SELECT generate_series(CURRENT_DATE - interval '400 days', CURRENT_DATE, '1 day') AS day) d
LEFT JOIN (
    SELECT date_trunc('day', date) AS day, brand,
           count(*) FILTER (WHERE NOT is_cancel) AS orders_cnt,
           sum(price_with_disc) FILTER (WHERE NOT is_cancel) AS orders_rub
    FROM wb_orders GROUP BY 1, 2
) o ON o.day = d.day
LEFT JOIN (
    SELECT date_trunc('day', date) AS day, brand,
           count(*) FILTER (WHERE NOT is_return) AS sales_cnt,
           sum(price_with_disc) FILTER (WHERE NOT is_return) AS sales_rub,
           count(*) FILTER (WHERE is_return) AS returns_cnt,
           sum(price_with_disc) FILTER (WHERE is_return) AS returns_rub
    FROM wb_sales GROUP BY 1, 2
) s ON s.day = d.day AND s.brand IS NOT DISTINCT FROM o.brand
LEFT JOIN (
    SELECT snapshot_date AS day, brand, sum(quantity) AS stock_qty
    FROM wb_stocks_daily GROUP BY 1, 2
) st ON st.day = d.day AND st.brand IS NOT DISTINCT FROM o.brand
WHERE o.brand IS NOT NULL OR s.brand IS NOT NULL OR st.brand IS NOT NULL;

-- v2 (2026-07-16): tick-model sync state
ALTER TABLE wb_sync_state ADD COLUMN IF NOT EXISTS blocked_until timestamptz;
ALTER TABLE wb_sync_state ADD COLUMN IF NOT EXISTS cursor_date date;
ALTER TABLE wb_sync_state ADD COLUMN IF NOT EXISTS done boolean NOT NULL DEFAULT false;
