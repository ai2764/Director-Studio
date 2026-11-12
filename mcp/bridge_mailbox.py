"""Durable shared mailbox for the Director Studio MCP bridge."""
from __future__ import annotations

import os
import sqlite3
from pathlib import Path

DB = Path(os.environ.get('DIRECTOR_MCP_MAILBOX', Path(__file__).resolve().parents[1] / '.run' / 'mcp-mailbox.sqlite3'))

class ClosingConnection(sqlite3.Connection):
    def __exit__(self, exc_type, exc, tb):
        try:
            return super().__exit__(exc_type, exc, tb)
        finally:
            self.close()

def connect():
    DB.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(DB, timeout=10, factory=ClosingConnection)
    db.row_factory = sqlite3.Row
    db.execute('PRAGMA busy_timeout=10000')
    db.execute('''CREATE TABLE IF NOT EXISTS messages (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        sender TEXT NOT NULL, recipient TEXT NOT NULL, subject TEXT NOT NULL,
        body TEXT NOT NULL, reply_to INTEGER REFERENCES messages(id),
        client_message_id TEXT, created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
        acknowledged_at TEXT, UNIQUE(sender, client_message_id)
    )''')
    return db

def send(sender: str, recipient: str, subject: str, body: str, reply_to: int | None = None, client_message_id: str | None = None) -> dict:
    sender, recipient, subject, body = (x.strip() for x in (sender, recipient, subject, body))
    if sender not in ('codex', 'muse') or recipient not in ('codex', 'muse') or sender == recipient:
        raise ValueError('sender and recipient must be different: codex or muse')
    if not subject or len(subject) > 200 or not body or len(body) > 20000:
        raise ValueError('subject must be 1-200 characters; body must be 1-20000 characters')
    if client_message_id is not None and (not client_message_id.strip() or len(client_message_id) > 128):
        raise ValueError('client_message_id must be 1-128 characters')
    with connect() as db:
        if reply_to is not None and not db.execute('SELECT 1 FROM messages WHERE id=?', (reply_to,)).fetchone():
            raise ValueError('reply_to message does not exist')
        if client_message_id:
            existing = db.execute('SELECT * FROM messages WHERE sender=? AND client_message_id=?', (sender, client_message_id)).fetchone()
            if existing:
                if (existing['recipient'], existing['subject'], existing['body'], existing['reply_to']) != (recipient, subject, body, reply_to):
                    raise ValueError('client_message_id already used for a different message')
                return dict(existing)
        cursor = db.execute('INSERT INTO messages (sender,recipient,subject,body,reply_to,client_message_id) VALUES (?,?,?,?,?,?)', (sender,recipient,subject,body,reply_to,client_message_id))
        return dict(db.execute('SELECT * FROM messages WHERE id=?', (cursor.lastrowid,)).fetchone())

def list_messages(recipient: str, after_id: int = 0, limit: int = 50, include_acknowledged: bool = True) -> dict:
    if recipient not in ('codex', 'muse') or after_id < 0:
        raise ValueError('recipient must be codex or muse and after_id must be non-negative')
    with connect() as db:
        rows = db.execute('SELECT * FROM messages WHERE recipient=? AND id>? AND (? OR acknowledged_at IS NULL) ORDER BY id LIMIT ?', (recipient, after_id, include_acknowledged, max(1,min(limit,100)))).fetchall()
        messages = [dict(row) for row in rows]
        return {'messages': messages, 'next_after_id': messages[-1]['id'] if messages else after_id}

def acknowledge(recipient: str, message_id: int) -> dict:
    if recipient not in ('codex', 'muse'):
        raise ValueError('recipient must be codex or muse')
    with connect() as db:
        cursor = db.execute("UPDATE messages SET acknowledged_at=COALESCE(acknowledged_at,strftime('%Y-%m-%dT%H:%M:%fZ','now')) WHERE id=? AND recipient=?", (message_id, recipient))
        if not cursor.rowcount: raise ValueError('message not found for recipient')
        return dict(db.execute('SELECT * FROM messages WHERE id=?', (message_id,)).fetchone())
