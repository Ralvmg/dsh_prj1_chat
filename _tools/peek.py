"""查看最近对话记录，确认模型是否描述了图片内容"""
import sqlite3

con = sqlite3.connect("/app/data/qqchat.db")
con.row_factory = sqlite3.Row
rows = con.execute(
    "SELECT role, nickname, content, created_at FROM messages ORDER BY id DESC LIMIT 12"
).fetchall()

for r in reversed(rows):
    role = "USER" if r["role"] == "user" else "BOT "
    c = (r["content"] or "").replace("\n", " / ")
    print(f"{role}: {c[:260]}")
