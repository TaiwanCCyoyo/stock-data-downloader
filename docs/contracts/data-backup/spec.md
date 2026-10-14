# data-backup Specification

## Purpose

Protect the `data/` cache with a copy that cannot be destroyed by an accident at the
source. The price data is acquired under a daily API quota, so losing it costs days of
re-downloading rather than minutes of restoring, and the machine has no OS-level safety
net (Volume Shadow Copy is off).

Two properties define this capability, because the obvious implementation of a backup
has neither. A mirror that deletes is not a backup: `robocopy /MIR` and `rsync --delete`
replicate a source-side deletion into the destination, turning one accident into two
lost copies -- which is exactly how ~4000 rate-limited per-symbol CSVs were lost on
2026-08-13. And a reported success is not a verified one: the same incident involved
tools that reported success while doing the wrong thing, and a backup nobody has checked
is an assumption that gets tested at the worst possible moment.

## Requirements

### Requirement: Backups never delete

The backup MUST be additive: it MUST copy new and changed files into the destination
and MUST NOT delete anything, from either the source or the destination, under any
option. A deletion at the source MUST NOT propagate into the backup.

Retention, where wanted, is a deliberate manual act at the storage location, not a
behavior of this tool.

#### Scenario: A file disappears from the source

- **WHEN** a file present in a previous backup no longer exists in `data/`
- **AND** the backup runs again
- **THEN** the copy in the destination MUST be left intact

#### Scenario: A file changes at the source

- **WHEN** a source file differs in size from its counterpart, or is newer
- **THEN** the backup MUST copy it over the destination copy

#### Scenario: Nothing has changed

- **WHEN** every source file already exists in the destination with a matching size
  and is no newer
- **THEN** the backup MUST copy nothing

### Requirement: Every backup verifies itself

After copying, the system MUST verify that each source file exists in the destination
with a matching byte count, and MUST fail with a non-zero status listing the problems
if any file is missing or differs.

Part of this cache is written continuously, so a size difference is only evidence of a
bad copy when the source has not advanced since that copy was taken.

#### Scenario: The destination is missing a file

- **WHEN** verification finds a source file absent from the destination
- **THEN** the run MUST fail and name the missing file

#### Scenario: A destination file was truncated

- **WHEN** a destination file's size differs from its source, and the source has not been
  modified since the destination copy was taken
- **THEN** the run MUST fail and name the mismatched file

#### Scenario: The source moved on after it was copied

- **WHEN** a destination file's size differs from its source, and the source has been
  modified since the destination copy was taken
- **THEN** the run MUST report that file as one snapshot behind rather than failing,
  because the copy is intact and the source is simply newer

### Requirement: The destination is explicit and outside the repository

The backup destination MUST come from an explicit `--backup-dir` argument or the
`SHIOAJI_BACKUP_DIR` environment variable, with no default. The system MUST refuse a
destination inside the repository or inside the data directory being backed up,
since a backup stored in the tree it protects shares that tree's fate. When the data
directory is configured outside the repository (`SHIOAJI_DATA_DIR`), its parent is
not treated as that tree.

#### Scenario: No destination configured

- **WHEN** neither `--backup-dir` nor `SHIOAJI_BACKUP_DIR` is set
- **THEN** the backup MUST be skipped with a warning rather than writing to a default
  location

#### Scenario: Destination inside the repository

- **WHEN** the destination resolves to a path inside the repository or the data
  directory
- **THEN** the run MUST fail and say why

### Requirement: Offsite archives are split by data type

The system MUST be able to produce compressed archives of the mirror, split by data
type and each labelled with the calendar quarter, so that a retention policy can keep
different depths of each.

Archives MUST be built from the verified mirror rather than the live cache, so that a
concurrent refresh cannot include a partially written file.

#### Scenario: Building archives

- **WHEN** archives are requested
- **THEN** the system MUST write `data_day_<YYYYQn>.tar.gz` containing daily bars and
  metadata, `data_min_<YYYYQn>.tar.gz` containing minute bars, and
  `data_meta_<YYYYQn>.tar.gz` containing the small SQLite artifacts that describe the
  bars (corporate actions, symbol metadata)
- **AND** all MUST be written under `<backup-dir>/archive`

#### Scenario: Archiving with no mirror present

- **WHEN** archives are requested but no mirror exists at the destination
- **THEN** the run MUST fail rather than produce an empty archive
