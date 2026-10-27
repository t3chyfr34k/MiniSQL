"""Read-only SQL Server schema introspection via pyodbc."""
import pyodbc


def load_schema(server, database, driver, trusted, config):
    if not server or not database or not driver:
        raise ValueError("Server, database and ODBC driver are required.")

    if trusted:
        conn_str = (
            f"DRIVER={{{driver}}};SERVER={server};DATABASE={database};"
            "Trusted_Connection=yes;TrustServerCertificate=yes;"
        )
    else:
        username = config.get("database", "username", fallback="")
        password = config.get("database", "password", fallback="")
        if not username or not password:
            raise ValueError(
                "SQL authentication selected but username/password are missing in config.ini. "
                "Use Windows authentication where possible."
            )
        conn_str = (
            f"DRIVER={{{driver}}};SERVER={server};DATABASE={database};"
            f"UID={username};PWD={password};TrustServerCertificate=yes;"
        )

    # Only catalog metadata is queried; no table data is read.
    with pyodbc.connect(conn_str, timeout=8, autocommit=True) as conn:
        cur = conn.cursor()
        cur.execute("""
            SELECT
                s.name AS schema_name,
                t.name AS table_name,
                c.name AS column_name,
                ty.name AS type_name,
                c.is_nullable,
                CASE WHEN pk.column_id IS NOT NULL THEN 1 ELSE 0 END AS is_primary_key,
                CASE WHEN fk.parent_column_id IS NOT NULL THEN 1 ELSE 0 END AS is_foreign_key
            FROM sys.tables t
            JOIN sys.schemas s ON s.schema_id = t.schema_id
            JOIN sys.columns c ON c.object_id = t.object_id
            JOIN sys.types ty ON ty.user_type_id = c.user_type_id
            LEFT JOIN (
                SELECT ic.object_id, ic.column_id
                FROM sys.indexes i
                JOIN sys.index_columns ic
                  ON ic.object_id = i.object_id AND ic.index_id = i.index_id
                WHERE i.is_primary_key = 1
            ) pk ON pk.object_id = c.object_id AND pk.column_id = c.column_id
            LEFT JOIN (
                SELECT parent_object_id, parent_column_id
                FROM sys.foreign_key_columns
            ) fk ON fk.parent_object_id = c.object_id AND fk.parent_column_id = c.column_id
            ORDER BY s.name, t.name, c.column_id;
        """)
        rows = cur.fetchall()

        cur.execute("""
            SELECT
                OBJECT_SCHEMA_NAME(fkc.parent_object_id) AS parent_schema,
                OBJECT_NAME(fkc.parent_object_id) AS parent_table,
                pc.name AS parent_column,
                OBJECT_SCHEMA_NAME(fkc.referenced_object_id) AS ref_schema,
                OBJECT_NAME(fkc.referenced_object_id) AS ref_table,
                rc.name AS ref_column
            FROM sys.foreign_key_columns fkc
            JOIN sys.columns pc ON pc.object_id = fkc.parent_object_id
                               AND pc.column_id = fkc.parent_column_id
            JOIN sys.columns rc ON rc.object_id = fkc.referenced_object_id
                               AND rc.column_id = fkc.referenced_column_id
            ORDER BY parent_schema, parent_table;
        """)
        fk_rows = cur.fetchall()

    tables = {}
    for r in rows:
        key = f"[{r.schema_name}].[{r.table_name}]"
        tables.setdefault(key, [])
        tags = []
        if r.is_primary_key:
            tags.append("PK")
        if r.is_foreign_key:
            tags.append("FK")
        if r.is_nullable:
            tags.append("NULL")
        else:
            tags.append("NOT NULL")
        tables[key].append(f"  {r.column_name} {r.type_name} ({', '.join(tags)})")

    lines = [f"Database: {database}", "SQL dialect: Microsoft SQL Server (T-SQL)", "Tables and columns:"]
    for table, cols in tables.items():
        lines.append(table)
        lines.extend(cols)
    lines.append("Foreign-key relationships:")
    if fk_rows:
        for r in fk_rows:
            lines.append(
                f"  [{r.parent_schema}].[{r.parent_table}].[{r.parent_column}] -> "
                f"[{r.ref_schema}].[{r.ref_table}].[{r.ref_column}]"
            )
    else:
        lines.append("  No foreign keys reported.")
    return "\n".join(lines)
