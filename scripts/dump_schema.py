import sqlite3

c = sqlite3.connect(r"backend\data\app.db")
tables = [r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")]
print("tables:", tables)
if "synclogs" in tables:
    print("synclogs schema:")
    for r in c.execute("PRAGMA table_info(synclogs)"):
        print(r)
if "users" in tables:
    print("users schema:")
    for r in c.execute("PRAGMA table_info(users)"):
        print(r)
c.close()