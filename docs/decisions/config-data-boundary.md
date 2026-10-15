# Code-owned categories and acquired symbol names

Move the versioned industry dictionary byte-for-byte from
`data/stock_category.json5` to `config/stock_category.json5`, addressed by the
existing `STOCK_CATEGORY` constant and included in the wheel. Keep the existing
missing-resource warning and fallback.

The mutable `stock_symbol_mapping.json5` already uses `DATA_DIR`, including its
writer's shrink-guard read and the data backup. Preserve that implementation;
consumers keep reading the filename under their configured shared producer root.
The category dictionary travels with Git and the wheel instead of data backups.

Historical paths in `docs/migration/source-files.csv` remain rebuild provenance.
No data relocation, cleanup, download or scheduler change is part of this change.
