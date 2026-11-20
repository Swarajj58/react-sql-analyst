import random
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

DB_PATH = Path(__file__).parent / "analytics.db"


def create_schema(conn: sqlite3.Connection) -> None:
    cursor = conn.cursor()
    cursor.executescript(
        """
        DROP TABLE IF EXISTS refunds;
        DROP TABLE IF EXISTS ord_hdr;
        DROP TABLE IF EXISTS cust_dim;

        CREATE TABLE cust_dim (
            cust_id INTEGER PRIMARY KEY,
            company_name TEXT NOT NULL,
            acct_tier TEXT NOT NULL,         -- 'ENT_PRO', 'MID_BIZ', 'SELF_SERVE'
            geo_mkt TEXT NOT NULL,           -- 'NA_WEST', 'NA_EAST', 'EMEA', 'APAC'
            signup_dt TEXT NOT NULL
        );

        CREATE TABLE ord_hdr (
            ord_id INTEGER PRIMARY KEY,
            cust_id INTEGER NOT NULL,
            ord_dt TEXT NOT NULL,            -- ISO-8601 YYYY-MM-DD
            gross_cents INTEGER NOT NULL,    -- Stored in integer cents
            ord_status TEXT NOT NULL,        -- 'SETTLED_NET', 'VOID_PRE_AUTH', 'PENDING_CAPTURE'
            prod_sku TEXT NOT NULL,
            FOREIGN KEY (cust_id) REFERENCES cust_dim(cust_id)
        );

        CREATE TABLE refunds (
            rfnd_id INTEGER PRIMARY KEY,
            ord_id INTEGER NOT NULL,
            rfnd_dt TEXT NOT NULL,
            rfnd_cents INTEGER NOT NULL,
            reason_code TEXT NOT NULL,       -- 'LATENCY_SLA_BREACH', 'BILLING_DUP', 'CUST_CHURN'
            FOREIGN KEY (ord_id) REFERENCES ord_hdr(ord_id)
        );

        CREATE INDEX idx_ord_dt ON ord_hdr(ord_dt);
        CREATE INDEX idx_ord_cust ON ord_hdr(cust_id);
        CREATE INDEX idx_rfnd_ord ON refunds(ord_id);
        """
    )
    conn.commit()


def seed_data(conn: sqlite3.Connection) -> None:
    rng = random.Random(42)  # Deterministic seed for reproducible evals
    cursor = conn.cursor()

    tiers = ["ENT_PRO", "MID_BIZ", "SELF_SERVE"]
    regions = ["NA_WEST", "NA_EAST", "EMEA", "APAC"]
    skus = ["CORE_API", "STREAM_PLUS", "DEDICATED_NODE"]

    # 1. Seed 120 customers
    customers = []
    base_signup = datetime(2025, 1, 1)
    for cid in range(1, 121):
        signup = (base_signup + timedelta(days=rng.randint(0, 450))).strftime("%Y-%m-%d")
        customers.append((
            cid,
            f"Acme_Corp_{cid:03d}",
            rng.choices(tiers, weights=[0.2, 0.35, 0.45])[0],
            rng.choice(regions),
            signup,
        ))
    cursor.executemany(
        "INSERT INTO cust_dim VALUES (?, ?, ?, ?, ?)",
        customers,
    )

    cust_region_map = {c[0]: c[3] for c in customers}

    # 2. Seed 1,500 orders across Q1-Q3 2026 (2026-01-01 to 2026-09-30)
    orders = []
    refunds = []
    start_dt = datetime(2026, 1, 1)
    rfnd_id = 1

    for oid in range(1, 1501):
        cid = rng.randint(1, 120)
        region = cust_region_map[cid]
        order_date = start_dt + timedelta(days=rng.randint(0, 272))
        ord_dt_str = order_date.strftime("%Y-%m-%d")
        quarter = (order_date.month - 1) // 3 + 1

        sku = rng.choice(skus)
        base_cents = {"CORE_API": 25000, "STREAM_PLUS": 75000, "DEDICATED_NODE": 180000}[sku]
        gross_cents = int(base_cents * rng.uniform(0.85, 1.25))

        status = rng.choices(
            ["SETTLED_NET", "VOID_PRE_AUTH", "PENDING_CAPTURE"],
            weights=[0.82, 0.12, 0.06],
        )[0]

        orders.append((oid, cid, ord_dt_str, gross_cents, status, sku))

        # 3. Seed refunds ONLY on settled orders, with a deliberate Q3 NA_WEST spike
        if status == "SETTLED_NET":
            is_q3_west = (region == "NA_WEST" and quarter == 3)
            refund_prob = 0.38 if is_q3_west else 0.05

            if rng.random() < refund_prob:
                rfnd_dt_str = (order_date + timedelta(days=rng.randint(2, 14))).strftime("%Y-%m-%d")
                rfnd_cents = gross_cents if is_q3_west else int(gross_cents * rng.choice([0.5, 1.0]))
                reason = (
                    "LATENCY_SLA_BREACH"
                    if is_q3_west and rng.random() < 0.85
                    else rng.choice(["BILLING_DUP", "CUST_CHURN"])
                )
                refunds.append((rfnd_id, oid, rfnd_dt_str, rfnd_cents, reason))
                rfnd_id += 1

    cursor.executemany("INSERT INTO ord_hdr VALUES (?, ?, ?, ?, ?, ?)", orders)
    cursor.executemany("INSERT INTO refunds VALUES (?, ?, ?, ?, ?)", refunds)
    conn.commit()
    print(f"Seeded {len(customers)} customers, {len(orders)} orders, {len(refunds)} refunds -> {DB_PATH}")


if __name__ == "__main__":
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(DB_PATH) as connection:
        create_schema(connection)
        seed_data(connection)