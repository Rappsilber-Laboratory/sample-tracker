import sqlite3

RENAMES = [
    ("D17001", "FAIMS"),
    ("D17002", "uPAC"),
    ("D17003", "LS_D17001"),
    ("D17004", "LS_D17002"),
    ("D17005", "TEV-E-DSSO"),
    ("D17006", "DCAF17-HTBH"),
]

conn = sqlite3.connect("samples.db")
conn.execute("PRAGMA foreign_keys = OFF")
for old, new in RENAMES:
    conn.execute("UPDATE mass_spec_sample SET experiment_code = ? WHERE experiment_code = ?", (new, old))
    conn.execute("UPDATE experiment SET code = ?, name = ? WHERE code = ?", (new, new, old))
conn.execute("PRAGMA foreign_keys = ON")
conn.commit()

for row in conn.execute("SELECT code, name FROM experiment WHERE project_code = 'D17' ORDER BY code"):
    print(row)
conn.close()
