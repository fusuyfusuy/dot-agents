use crate::model::{Symbol, SymbolKind};
use anyhow::Result;
use rusqlite::{params, Connection};
use std::path::Path;

/// Bumped whenever parsing or the stored symbol/reference format changes.
///
/// Without this, a format change decodes through `unwrap_or_default()` at load
/// time and yields an empty reference list rather than an error -- a graph with
/// zero edges, on a database that reports itself as fresh.
///
/// v7: path-alias imports (`tsconfig.json` `compilerOptions.paths`) are no
/// longer classified as external packages. Cached rows carry the old, wrong
/// `external_imports`, so they must go.
pub const PARSER_VERSION: i64 = 7;

pub struct Database {
    conn: Connection,
}

impl Database {
    pub fn open_or_create(db_path: &Path) -> Result<Self> {
        if let Some(parent) = db_path.parent() {
            std::fs::create_dir_all(parent)?;
        }

        let conn = Connection::open(db_path)?;
        conn.execute_batch(
            "PRAGMA journal_mode = WAL;
             PRAGMA synchronous = NORMAL;
             PRAGMA foreign_keys = ON;

             CREATE TABLE IF NOT EXISTS files (
                 id INTEGER PRIMARY KEY AUTOINCREMENT,
                 path TEXT UNIQUE NOT NULL,
                 mtime INTEGER NOT NULL,
                 hash TEXT NOT NULL
             );

             CREATE TABLE IF NOT EXISTS symbols (
                 id INTEGER PRIMARY KEY AUTOINCREMENT,
                 file_id INTEGER NOT NULL,
                 name TEXT NOT NULL,
                 kind TEXT NOT NULL,
                 start_line INTEGER NOT NULL,
                 end_line INTEGER NOT NULL,
                 signature TEXT NOT NULL,
                 body TEXT NOT NULL,
                 centrality REAL DEFAULT 0.0,
                 references_json TEXT DEFAULT '[]',
                 mentions_json TEXT DEFAULT '[]',
                 call_counts_json TEXT DEFAULT '{}',
                 member_calls_json TEXT DEFAULT '[]',
                 external_imports_json TEXT DEFAULT '[]',
                 FOREIGN KEY(file_id) REFERENCES files(id) ON DELETE CASCADE
             );

             CREATE INDEX IF NOT EXISTS idx_symbols_name ON symbols(name);
             CREATE INDEX IF NOT EXISTS idx_symbols_file_id ON symbols(file_id);
             CREATE INDEX IF NOT EXISTS idx_symbols_centrality ON symbols(centrality DESC);",
        )?;
        // Existing DBs created before v6 lack the new columns; the version
        // bump wipes rows but not schema, so add them idempotently.
        for alter in [
            "ALTER TABLE symbols ADD COLUMN mentions_json TEXT DEFAULT '[]'",
            "ALTER TABLE symbols ADD COLUMN call_counts_json TEXT DEFAULT '{}'",
            "ALTER TABLE symbols ADD COLUMN member_calls_json TEXT DEFAULT '[]'",
            "ALTER TABLE symbols ADD COLUMN external_imports_json TEXT DEFAULT '[]'",
        ] {
            let _ = conn.execute(alter, []);
        }

        let db = Database { conn };
        db.enforce_parser_version()?;
        db.init_alias_fingerprint()?;
        Ok(db)
    }

    /// Discard everything cached by an older parser. Deleting from `files`
    /// cascades to `symbols`.
    fn enforce_parser_version(&self) -> Result<()> {
        let found: i64 = self
            .conn
            .query_row("PRAGMA user_version", [], |row| row.get(0))?;

        if found != PARSER_VERSION {
            self.conn.execute("DELETE FROM files", [])?;
            self.conn
                .execute_batch(&format!("PRAGMA user_version = {PARSER_VERSION};"))?;
        }

        Ok(())
    }

    /// Alias fingerprints are workspace-level config, so `files` rows cannot
    /// carry the invalidation. Editing `tsconfig.json` to add an alias must
    /// re-parse, or the index keeps serving the alias-blind answer -- the
    /// silent-wrong-answer class this whole path exists to remove.
    fn init_alias_fingerprint(&self) -> Result<()> {
        self.conn.execute_batch(
            "CREATE TABLE IF NOT EXISTS meta (
                 key TEXT PRIMARY KEY,
                 value TEXT NOT NULL
             );",
        )?;
        Ok(())
    }

    /// Drop the cached parse when the workspace's alias config changed.
    pub fn enforce_alias_fingerprint(&self, fingerprint: &str) -> Result<()> {
        let stored: Option<String> = self
            .conn
            .query_row(
                "SELECT value FROM meta WHERE key = 'alias_fingerprint'",
                [],
                |row| row.get(0),
            )
            .ok();
        if stored.as_deref() == Some(fingerprint) {
            return Ok(());
        }
        self.conn.execute("DELETE FROM files", [])?;
        self.conn.execute(
            "INSERT INTO meta (key, value) VALUES ('alias_fingerprint', ?1)
             ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            params![fingerprint],
        )?;
        Ok(())
    }

    pub fn get_file_records(
        &self,
    ) -> Result<std::collections::HashMap<String, (i64, i64, String)>> {
        let mut stmt = self
            .conn
            .prepare("SELECT id, path, mtime, hash FROM files")?;
        let rows = stmt.query_map([], |row| {
            Ok((
                row.get::<_, String>(1)?,
                (
                    row.get::<_, i64>(0)?,
                    row.get::<_, i64>(2)?,
                    row.get::<_, String>(3)?,
                ),
            ))
        })?;

        let mut map = std::collections::HashMap::new();
        for r in rows {
            let (path, data) = r?;
            map.insert(path, data);
        }
        Ok(map)
    }

    pub fn save_file_and_symbols(
        &mut self,
        file_path: &str,
        mtime: i64,
        hash: &str,
        symbols: &[Symbol],
    ) -> Result<()> {
        let tx = self.conn.transaction()?;

        // Delete existing file entry if present
        tx.execute("DELETE FROM files WHERE path = ?", params![file_path])?;

        tx.execute(
            "INSERT INTO files (path, mtime, hash) VALUES (?, ?, ?)",
            params![file_path, mtime, hash],
        )?;
        let file_id = tx.last_insert_rowid();

        {
            let mut stmt = tx.prepare_cached(
                "INSERT INTO symbols (file_id, name, kind, start_line, end_line, signature, body, centrality, references_json, mentions_json, call_counts_json, member_calls_json, external_imports_json)
                 VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            )?;

            for s in symbols {
                // references_json stores calls (column name kept for schema stability).
                let calls_json =
                    serde_json::to_string(&s.calls).unwrap_or_else(|_| "[]".to_string());
                let mentions_json =
                    serde_json::to_string(&s.mentions).unwrap_or_else(|_| "[]".to_string());
                let counts_json =
                    serde_json::to_string(&s.call_counts).unwrap_or_else(|_| "{}".to_string());
                let member_json =
                    serde_json::to_string(&s.member_calls).unwrap_or_else(|_| "[]".to_string());
                let imports_json =
                    serde_json::to_string(&s.external_imports).unwrap_or_else(|_| "[]".to_string());
                stmt.execute(params![
                    file_id,
                    s.name,
                    s.kind.as_str(),
                    s.start_line as i64,
                    s.end_line as i64,
                    s.signature,
                    s.body,
                    s.centrality,
                    calls_json,
                    mentions_json,
                    counts_json,
                    member_json,
                    imports_json
                ])?;
            }
        }

        tx.commit()?;
        Ok(())
    }

    pub fn delete_file_by_id(&self, file_id: i64) -> Result<()> {
        self.conn
            .execute("DELETE FROM files WHERE id = ?", params![file_id])?;
        Ok(())
    }

    pub fn load_all_symbols(&self) -> Result<Vec<Symbol>> {
        let mut stmt = self.conn.prepare(
            "SELECT s.name, s.kind, f.path, s.start_line, s.end_line, s.signature, s.body, s.centrality, s.references_json, s.mentions_json, s.call_counts_json, s.member_calls_json, s.external_imports_json
             FROM symbols s JOIN files f ON s.file_id = f.id
             ORDER BY s.file_id, s.start_line, s.id",
        )?;
        let rows = stmt.query_map([], |row| {
            let kind = parse_symbol_kind(row.get(1)?);
            let calls_str: String = row.get(8)?;
            // PARSER_VERSION guards format changes; this guards corruption.
            // Silently defaulting here yields an edgeless graph on a database
            // that reports itself as fresh.
            let calls: Vec<String> = serde_json::from_str(&calls_str).map_err(|e| {
                rusqlite::Error::FromSqlConversionFailure(
                    8,
                    rusqlite::types::Type::Text,
                    Box::new(e),
                )
            })?;
            let json_vec = |idx: usize| -> rusqlite::Result<Vec<String>> {
                let s: String = row.get(idx)?;
                serde_json::from_str(&s).map_err(|e| {
                    rusqlite::Error::FromSqlConversionFailure(
                        idx,
                        rusqlite::types::Type::Text,
                        Box::new(e),
                    )
                })
            };
            let mentions = json_vec(9)?;
            let call_counts_str: String = row.get(10)?;
            let call_counts: std::collections::HashMap<String, u32> =
                serde_json::from_str(&call_counts_str).map_err(|e| {
                    rusqlite::Error::FromSqlConversionFailure(
                        10,
                        rusqlite::types::Type::Text,
                        Box::new(e),
                    )
                })?;
            let member_calls = json_vec(11)?;
            let external_imports = json_vec(12)?;
            Ok(Symbol {
                name: row.get(0)?,
                kind,
                file: row.get(2)?,
                start_line: row.get::<_, i64>(3)? as usize,
                end_line: row.get::<_, i64>(4)? as usize,
                signature: row.get(5)?,
                body: row.get(6)?,
                centrality: row.get(7)?,
                calls,
                mentions,
                call_counts,
                member_calls,
                external_imports,
            })
        })?;

        let mut symbols = Vec::new();
        for r in rows {
            symbols.push(r?);
        }
        Ok(symbols)
    }
}

fn parse_symbol_kind(raw: String) -> SymbolKind {
    match raw.as_str() {
        "function" => SymbolKind::Function,
        "method" => SymbolKind::Method,
        "struct" => SymbolKind::Struct,
        "class" => SymbolKind::Class,
        "interface" => SymbolKind::Interface,
        "trait" => SymbolKind::Trait,
        "enum" => SymbolKind::Enum,
        "type" => SymbolKind::TypeAlias,
        "variable" => SymbolKind::Variable,
        "constant" => SymbolKind::Constant,
        "field" => SymbolKind::Field,
        _ => SymbolKind::Module,
    }
}
