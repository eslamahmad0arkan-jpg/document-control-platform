import sqlite3
import threading
import time

DB = r"backend\data\app.db"
N_THREADS = 8
DURATION = 12.0
errors = []
lock = threading.Lock()


def writer(name):
    try:
        c = sqlite3.connect(DB, timeout=25)
        c.execute("PRAGMA journal_mode=WAL")
        c.execute("PRAGMA busy_timeout=25000")
        end = time.time() + DURATION
        n = 0
        while time.time() < end:
            c.execute(
                "UPDATE users SET last_login_at=datetime('now') WHERE id=1"
            )
            c.commit()
            c.execute(
                "INSERT INTO _stress (name, n) VALUES (?, ?)",
                (name, n),
            )
            c.commit()
            n += 1
        c.close()
        with lock:
            errors.append(f"{name}: wrote {n} rows OK")
    except Exception as e:  # noqa: BLE001
        with lock:
            errors.append(f"{name}: FAILED {type(e).__name__}: {e}")


def main():
    c0 = sqlite3.connect(DB, timeout=25)
    c0.execute("PRAGMA journal_mode=WAL")
    c0.executescript(
        "CREATE TABLE IF NOT EXISTS _stress (id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "name TEXT, n INTEGER, ts DATETIME DEFAULT CURRENT_TIMESTAMP)"
    )
    c0.commit()
    c0.close()

    threads = [threading.Thread(target=writer, args=(f"t{i}",)) for i in range(N_THREADS)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    print("\n".join(errors))
    bad = [e for e in errors if "FAILED" in e]
    print(f"TOTAL FAILURES: {len(bad)}")

    c = sqlite3.connect(DB)
    rows = c.execute("SELECT COUNT(*) FROM _stress").fetchone()[0]
    print(f"rows inserted during stress: {rows}")
    c.execute("DROP TABLE _stress")
    c.commit()
    c.close()
    print("cleanup done")


if __name__ == "__main__":
    main()