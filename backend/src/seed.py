"""Seed data for MyCitiPad property listings.

Replace with a sync from the live site or the property feed when available.
"""

import logging
import sqlite3

logger = logging.getLogger("mycitipad.seed")

# Seed rows: Real listings from MyCitiPad website plus demo lettings for interactive viewing testing.
SEED_PROPERTIES = [
    # Sales listings (available)
    {
        "property_id": "prop-sale-turnstone",
        "address": "Turnstone Drive, DN4",
        "postcode_area": "DN4",
        "department": "sales",
        "status": "available",
        "bedrooms": 3,
        "price_gbp": 250000,
        "source_url": "https://mycitipad.com/sales/turnstone-drive",
        "is_demo": 0,
    },
    {
        "property_id": "prop-sale-snowdrop",
        "address": "Snowdrop Mead",
        "postcode_area": None,
        "department": "sales",
        "status": "available",
        "bedrooms": 4,
        "price_gbp": 320000,
        "source_url": "https://mycitipad.com/sales/snowdrop-mead",
        "is_demo": 0,
    },
    {
        "property_id": "prop-sale-blackberry",
        "address": "Blackberry Way, TA6",
        "postcode_area": "TA6",
        "department": "sales",
        "status": "available",
        "bedrooms": 3,
        "price_gbp": 285000,
        "source_url": "https://mycitipad.com/sales/blackberry-way",
        "is_demo": 0,
    },
    {
        "property_id": "prop-sale-golden-miller",
        "address": "Golden Miller Avenue, CM22",
        "postcode_area": "CM22",
        "department": "sales",
        "status": "available",
        "bedrooms": 4,
        "price_gbp": 475000,
        "source_url": "https://mycitipad.com/sales/golden-miller-avenue",
        "is_demo": 0,
    },
    {
        "property_id": "prop-sale-palermo",
        "address": "Palermo Road, NW10",
        "postcode_area": "NW10",
        "department": "sales",
        "status": "available",
        "bedrooms": 2,
        "price_gbp": 650000,
        "source_url": "https://mycitipad.com/sales/palermo-road",
        "is_demo": 0,
    },
    {
        "property_id": "prop-sale-barons",
        "address": "Barons Keep, W14",
        "postcode_area": "W14",
        "department": "sales",
        "status": "available",
        "bedrooms": 2,
        "price_gbp": 850000,
        "source_url": "https://mycitipad.com/sales/barons-keep",
        "is_demo": 0,
    },
    {
        "property_id": "prop-sale-redlands",
        "address": "Redlands Way, SN3",
        "postcode_area": "SN3",
        "department": "sales",
        "status": "available",
        "bedrooms": 3,
        "price_gbp": 230000,
        "source_url": "https://mycitipad.com/sales/redlands-way",
        "is_demo": 0,
    },
    # Real lettings listings (let subject to references on site)
    {
        "property_id": "prop-let-cascade",
        "address": "Cascade Court, Chelsea Bridge Wharf, SW11",
        "postcode_area": "SW11",
        "department": "lettings",
        "status": "let_subject_to_references",
        "bedrooms": 2,
        "price_gbp": 3200,
        "source_url": "https://mycitipad.com/lettings/cascade-court",
        "is_demo": 0,
    },
    {
        "property_id": "prop-let-long-street",
        "address": "Long Street, E2",
        "postcode_area": "E2",
        "department": "lettings",
        "status": "let_subject_to_references",
        "bedrooms": 2,
        "price_gbp": 2600,
        "source_url": "https://mycitipad.com/lettings/long-street",
        "is_demo": 0,
    },
    {
        "property_id": "prop-let-fisher-close",
        "address": "Fisher Close, SE16",
        "postcode_area": "SE16",
        "department": "lettings",
        "status": "let_subject_to_references",
        "bedrooms": 1,
        "price_gbp": 2100,
        "source_url": "https://mycitipad.com/lettings/fisher-close",
        "is_demo": 0,
    },
    # Fictional demo lettings (available for scheduling viewings in demo)
    {
        "property_id": "prop-demo-example-st",
        "address": "Example Street, SE1",
        "postcode_area": "SE1",
        "department": "lettings",
        "status": "available",
        "bedrooms": 1,
        "price_gbp": 2200,
        "source_url": "https://mycitipad.com/demo/example-street",
        "is_demo": 1,
    },
    {
        "property_id": "prop-demo-st-george",
        "address": "St George Wharf, SW8",
        "postcode_area": "SW8",
        "department": "lettings",
        "status": "available",
        "bedrooms": 2,
        "price_gbp": 2750,
        "source_url": "https://mycitipad.com/demo/st-george-wharf",
        "is_demo": 1,
    },
    {
        "property_id": "prop-demo-canary-riv",
        "address": "Canary Riverside, E14",
        "postcode_area": "E14",
        "department": "lettings",
        "status": "available",
        "bedrooms": 2,
        "price_gbp": 3100,
        "source_url": "https://mycitipad.com/demo/canary-riverside",
        "is_demo": 1,
    },
]


def seed_properties(conn: sqlite3.Connection) -> None:
    """Populate properties table idempotently."""
    cursor = conn.cursor()
    for prop in SEED_PROPERTIES:
        cursor.execute(
            """
            INSERT OR IGNORE INTO properties (
                property_id, address, postcode_area, department,
                status, bedrooms, price_gbp, source_url, is_demo
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                prop["property_id"],
                prop["address"],
                prop["postcode_area"],
                prop["department"],
                prop["status"],
                prop["bedrooms"],
                prop["price_gbp"],
                prop["source_url"],
                prop["is_demo"],
            ),
        )
    conn.commit()
    logger.debug("Properties seeded (%d properties ensured)", len(SEED_PROPERTIES))
