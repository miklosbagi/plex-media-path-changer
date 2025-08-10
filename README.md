# Plex Media Path Changer (PMPC)

A small, safe Python utility to update **base path segments** inside Plex's main SQLite database.  
Useful if you’ve moved or renamed the root of your media library (for example `/data/archive-media/...` → `/data/media/...`) and want Plex to reflect that change without re-adding everything.

## Features

- Direct SQLite editing — updates the correct Plex tables (`media_parts.file`, `section_locations.root_path`, etc.)
- Automatic DB backup — creates timestamped `.bak` copies before touching anything
- Dry run mode — preview all matches before committing changes
- Extension-safe — skips Plex’s virtual tables (`spellfix1`, FTS tokenizers) to avoid errors
- Integrity check — runs `PRAGMA integrity_check` where possible
- VACUUM — optimizes the database after updates

## Requirements

- Python 3.7+
- Plex Media Server stopped while running the script
- Read/write access to Plex’s database directory

## Installation

Clone or download this repository, then place `pmpc.py` somewhere on your system.
No external Python dependencies are required — it uses only the standard library.

## Usage

### 1. Stop Plex

Stop your Plex server so the database is not in use.

    docker stop plex

### 2. Locate the database

Find your `com.plexapp.plugins.library.db` file. Common location (Docker host path):

    /path/to/plex/config/Plug-in Support/Databases

### 3. Dry run

Preview what will change:

    python3 pmpc.py \
      --db-dir "/path/to/plex/config/Plug-in Support/Databases" \
      --old "archive-media" \
      --new "media" \
      --dry-run

### 4. Apply changes

If the dry run looks correct, run without `--dry-run`:

    python3 pmpc.py \
      --db-dir "/path/to/plex/config/Plug-in Support/Databases" \
      --old "archive-media" \
      --new "media"

The script will:

- Back up the DB and any `-wal`/`-shm` files into a `backups/` subfolder
- Update matching paths in relevant tables
- Commit the transaction and run a database `VACUUM`

### 5. Restart Plex

    docker start plex

Then in Plex:

- Refresh libraries
- Optionally, empty trash and optimize database from server settings

---

## CLI Options

| Option         | Description |
|----------------|-------------|
| `--db-dir`     | Directory containing `com.plexapp.plugins.library.db` |
| `--db-file`    | Path directly to `com.plexapp.plugins.library.db` (overrides `--db-dir`) |
| `--old`        | Old path segment to replace (required) |
| `--new`        | New path segment to insert (required) |
| `--dry-run`    | Preview matches without modifying the database |

## Examples

Replace a base directory name:

    python3 pmpc.py \
      --db-dir "/plex/config/Plug-in Support/Databases" \
      --old "archive-media" \
      --new "media"

Replace only when it’s a whole path segment:

    python3 pmpc.py \
      --db-dir "/plex/config/Plug-in Support/Databases" \
      --old "/archive-media/" \
      --new "/media/"
      
## Notes

- Always stop Plex before running PMPC.
- The backups are stored alongside the DB in a `backups/` folder.
- If something goes wrong, restore from the `.bak` file and restart Plex.
- PMPC **does not** touch Plex’s virtual/FTS/spellfix tables to avoid extension errors.
- If you have multiple libraries with different roots, run a dry run to confirm matches before committing.

## License

MIT
