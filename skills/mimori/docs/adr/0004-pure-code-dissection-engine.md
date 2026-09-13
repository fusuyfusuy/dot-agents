# 4. Pure Code Dissection Engine (Retire Action Journal & Snapshot Dumps)

We decided to strip out activity journaling (`mimori log`), workspace action history (`.mimori/activity.jsonl`), and context snapshot dumping (`mimori dump`), keeping `mimori` focused strictly on AST parsing, codebase crawling, and code dissection (`slice`, `find`, `up`, `down`, `uses`, `blast`, `map`, `missing`, `doctor`).

Action logging and decision tracking belong to higher-level agent coordination layers and knowledge substrates (such as `akatsuki` and version control commit histories), not to the low-level code dissection binary. Eliminating file-append mutations, journal rotation logic, and snapshot markdown assembly keeps the CLI minimal, avoids state bloat inside `.mimori/`, and enforces Unix single-responsibility principles.
