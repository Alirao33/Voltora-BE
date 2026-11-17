"""Copy every table from a SQLite database into PostgreSQL (or any other SQLAlchemy URL).

    python scripts/sqlite_to_postgres.py sqlite:///./voltora.db postgresql+psycopg2://user:pass@host:5432/voltora

The target must be empty. Tables are created from the ORM models, rows are copied in
foreign-key order, then Postgres id sequences are advanced past the copied ids.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import create_engine, func, select, text  # noqa: E402


def migrate(src_url: str, dst_url: str, batch: int = 1000) -> dict[str, int]:
    # Importing config.database builds an engine from DATABASE_URL; we only need the models' metadata.
    from config.database import Base  # noqa: E402
    import models  # noqa: F401,E402

    src, dst = create_engine(src_url), create_engine(dst_url)
    Base.metadata.create_all(dst)
    copied: dict[str, int] = {}
    with src.connect() as s, dst.begin() as d:
        for table in Base.metadata.sorted_tables:  # parents before children
            if d.scalar(select(func.count()).select_from(table)):
                raise SystemExit(f"Target table {table.name} is not empty; migrate into an empty database.")
            n = 0
            for rows in iter(lambda r=s.execute(select(table)): [dict(x._mapping) for x in r.fetchmany(batch)], []):
                d.execute(table.insert(), rows)
                n += len(rows)
            copied[table.name] = n
            pk = list(table.primary_key.columns)
            if dst.dialect.name == "postgresql" and n and len(pk) == 1 and pk[0].type.python_type is int:
                d.execute(text(
                    f"SELECT setval(pg_get_serial_sequence('{table.name}', '{pk[0].name}'), "
                    f"(SELECT MAX({pk[0].name}) FROM {table.name}))"
                ))
    return copied


if __name__ == "__main__":
    if len(sys.argv) != 3:
        raise SystemExit(__doc__)
    for name, n in migrate(sys.argv[1], sys.argv[2]).items():
        print(f"{name:28s}{n:>8d}")
