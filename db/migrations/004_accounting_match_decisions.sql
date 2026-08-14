CREATE TABLE IF NOT EXISTS accounting_match_decisions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    anchor_document_id TEXT NOT NULL,
    related_document_id TEXT NOT NULL,
    relationship_type TEXT NOT NULL
        CHECK (relationship_type IN (
            'quotation_to_purchase_order',
            'purchase_order_to_delivery',
            'purchase_order_to_invoice',
            'purchase_order_to_credit_note',
            'purchase_order_to_payment'
        )),
    decision TEXT NOT NULL CHECK (decision IN ('confirmed', 'rejected')),
    note TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
    FOREIGN KEY (anchor_document_id) REFERENCES documents(id) ON DELETE CASCADE,
    FOREIGN KEY (related_document_id) REFERENCES documents(id) ON DELETE CASCADE,
    UNIQUE (user_id, anchor_document_id, related_document_id, relationship_type),
    CHECK (anchor_document_id <> related_document_id)
);

CREATE INDEX IF NOT EXISTS idx_accounting_match_decisions_user
    ON accounting_match_decisions (user_id, anchor_document_id, relationship_type);
