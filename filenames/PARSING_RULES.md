# Filename / batch_name parsing rules

These rules govern how raw MS-run filenames in the `file` table are mapped onto
`experiment` and `sample` rows. They are applied **evenly** — no merging,
deduplication, or special-case fixups for typos or duplicate file rows.

## Filename schema (from `filter_matching.py`)

```
<InstrumentInitial>_<Date>_<ProjectCode>_<UserInitials>_<BatchName>
```

`BatchName` is what we further parse below; the rest is captured in the
existing columns of the `file` table.

## BatchName sub-template

For plate-based MS experiments (the dominant pattern in this database),
`BatchName` follows:

```
<ExperimentCode>_<SampleDescriptor>_S<plate>-<well>_1_<run>
```

Tokens, left to right:

- **ExperimentCode** — the first underscore-separated token of `BatchName`.
  Examples: `D17001`, `SCV003`, `QPT3008`. Used as `experiment.code`.
- **SampleDescriptor** — everything between the experiment code and the plate
  token. Identifies the sample within the experiment; multiple wells of the
  same sample share an identical descriptor. Examples: `8_60_sample_1`,
  `5_80_DIA_005ngHeLa`, `80_SPL_02`.
- **`S<plate>-<well>_1_<run>` tail** — invariant structure. `<plate>` is
  `S\d+`; `<well>` is `[A-Z]\d+`; the literal `1` never varies; `<run>` is the
  run-id integer.

If a `BatchName` does not match this template, it is **not** linked by the
bulk linker scripts — it stays unlinked rather than being force-fit.

## Per-project grouping

Within a project:

- **Experiment** — one row per distinct `ExperimentCode`, regardless of date.
  An experiment may span multiple dates (e.g. SCV003 in QPT runs on 20241221
  and 20250120 — both belong to the same experiment row).
- **Sample** — one row per distinct `(ExperimentCode, SampleDescriptor)` pair.
  All files sharing the pair link to the same sample via `file.sample_id`.
- **No deduplication** — duplicate file rows (identical `batch_name`) are
  linked to the same sample without being collapsed or removed.
- **No typo merging** — distinct first tokens that differ by a single character
  (e.g. `SLP3005` vs `SLPT3005` in QPT) become distinct experiments.

## Naming conventions

- `sample.code` = `SampleDescriptor` verbatim (underscores preserved).
- `sample.name` = `SampleDescriptor` with `_` replaced by space.
- `experiment.code` = `ExperimentCode` verbatim.
- `experiment.name` = `ExperimentCode` with a space inserted between the
  alphabetic prefix and the trailing digits, matching the pre-existing
  format used for D17001 / D17002 (`D17001` → `D17 001`,
  `SCV004` → `SCV 004`, `QPT3008` → `QPT 3008`).

## Sample type

Default: `IdentificationSample` (`crosslinked_sample = 0`).
`sample.file_name_root` is left NULL; the explicit `file.sample_id` foreign
key is the source of truth for which files belong to which sample.

## Scripts that apply this rule

- `link_d17001_20240815.py` — experiment D17001 (8 files → 8 samples).
- `link_d17002_20240817.py` — experiment D17002 (40 files → 8 multi-well samples).
- `link_qpt_samples.py` — all of project QPT, applied in bulk
  (1998 files → 184 samples across 23 experiments).

# Project-specific rules (non-QPT-style batch_names)

Some projects have batch_names that do **not** follow the QPT structural
template above. For those, a per-project routing rule is documented here and
applied by a dedicated linker script.

## D17 (dates 240729, 240806, 250616, 260109)

The earliest two D17 dates were already linked by `link_d17001_20240815.py`
(D17001) and `link_d17002_20240817.py` (D17002). The remaining four dates
each get their own experiment with codes assigned chronologically:

| Date    | Experiment | Files | Samples | Sample-grouping rule |
|---------|------------|-------|---------|----------------------|
| 240729  | `D17003`   | 15    | 7       | `_DIA_pool-K` → `DIA_pool` (3 reps); `_pepSECfN-K` → `pepSECfN` (2 reps each) |
| 240806  | `D17004`   | 30    | 10      | `_DIA_(pool\|FN)-K` → `pool` / `FN` (3 reps each) |
| 250616  | `D17005`   | 18    | 7       | `_(pool\|pepSEC_N)_repK` → `pool` / `pepSEC_N` (2-3 reps each) |
| 260109  | `D17006`   | 36    | 36      | `_DCAF17-HTBH_PD_<well>` → `<well>` (no replicates to collapse) |

Within each experiment, **replicates collapse to a single sample** (matching
the D17001 / D17002 multi-file-per-sample style). `sample.code` is the
biological-unit token from the parser; `sample.name` is the same with `_` →
` `; `crosslinked_sample = 0`. Applied by `link_d17_remaining.py`
(99 files → 60 samples across 4 experiments).

## LMB

LMB has two dates that are different studies → two experiments, codes by
chronological order. Leading column-id token (`60_LiPMS9_`, `104_LiPMS9_`)
is stripped from sample codes (constant within each experiment). No
replicates to collapse — 1 sample per distinct batch_name.

| Date    | Experiment | Files | Samples | Sample-grouping rule |
|---------|------------|-------|---------|----------------------|
| 250415  | `LMB001`   | 4     | 4       | `60_LiPMS9_(CSA-[EN](_DIA)?)` → `CSA-E`, `CSA-E_DIA`, `CSA-N`, `CSA-N_DIA` |
| 250613  | `LMB002`   | 8     | 8       | `104_LiPMS9_(Eltrombopag-K[LH])` and `104_LiPMS9_(Lactate-K_<conc>mM)` → trailing condition verbatim |

Applied by `link_lmb_samples.py` (12 files → 12 samples).

## SDA

SDA has one date (240724), 12 files. Leading two tokens (`<run>_<column>_`)
are run-order and column-id (47 vs 101 vary per row), discarded. Replicate
suffix `_repN` collapses; acquisition mode (DDA/DIA) stays as part of
sample identity. **One experiment** (`SDA001`, "SDA 001"), 6 samples:

| batch_name pattern                        | sample.code              | files |
|-------------------------------------------|--------------------------|-------|
| `<n>_<col>_HeLa_(DDA\|DIA)`               | `HeLa_DDA`, `HeLa_DIA`   | 1+1   |
| `<n>_<col>_Control_SDA-DTB_DDA_repK`      | `Control_SDA-DTB_DDA`    | 3     |
| `<n>_<col>_Enrichment_SDA-DTB_DDA_repK`   | `Enrichment_SDA-DTB_DDA` | 3     |
| `<n>_<col>_pepSEC_N_repK`                 | `pepSEC_6`, `pepSEC_7`   | 3+1   |

Applied by `link_sda_samples.py` (12 files → 6 samples).

## LEU

LEU has two dates that are different studies (different proteins) →
two experiments, codes assigned chronologically:

| Date    | Experiment | Files | Samples | Sample-grouping rule |
|---------|------------|-------|---------|----------------------|
| 251209  | `LEU001`   | 9     | 8       | `90_atpG_IDs_K` → `atpG_IDs` (2 reps); `90_atpG_pepSEC_fN` → `atpG_pepSEC_fN` (6 fractions × 1); `HelaQC` → `HelaQC` (instrument QC, kept under LEU001 per user) |
| 260329  | `LEU002`   | 6     | 6       | `atpD_Flag_pepSECfN` → `atpD_Flag_pepSECfN` (6 fractions × 1, verbatim) |

Applied by `link_leu_samples.py` (15 files → 14 samples across 2 experiments).
The inconsistent fraction-naming between dates (`pepSEC_f5` vs `pepSECf5`)
is preserved verbatim — no typo merging across studies.

## CCL

CCL is a clean grid on a single date (241205): 2 prep samples
(`CollagenS1`, `CollagenS4`) × 6 SEC fractions (f6–f11) × 3 replicates =
36 files. Modelled as **one experiment** (`CCL001`, "CCL 001") with **one
sample per `(prep, fraction)` pair** (replicates collapse, D17 style):

| batch_name pattern               | sample.code        |
|----------------------------------|--------------------|
| `100_(CollagenSN-fK)_repM`       | `CollagenSN-fK`    |

12 samples × 3 files each = 36 files. Applied by `link_ccl_samples.py`.

## Small projects (uniform fallback)

`ERM`, `SPB`, `PLX`, `UVL`, `LMP`, `AMX`, `WTC` — each has ≤7 files.
Volume too low to justify custom parsing rules. **Uniform fallback**:
one experiment per project (code `<PROJECT>001`, name `<PROJECT> 001`),
one sample per distinct `batch_name` (verbatim — underscores preserved
in `sample.code`, replaced by space in `sample.name`).
Applied by `link_small_projects.py` (26 files → 25 samples across 7 experiments).

The code `SPB` is non-unique in the database (two project rows: id=10
'BS3 crosslinked SPB' and id=40 'Septin Borg2 SDA crosslinking'). The
unlinked SPB files contain `SeptinBORG` in their batch_names, so they
were attached to id=40 via an explicit `PROJECT_ID_OVERRIDES` map in the
script (user-confirmed).

## BNC ("Blank and cleaning")

The project's whole purpose is tracking blanks / column-cleaning / corrupted
runs, so the heterogeneous batch_names ARE the data. All 18 files have
distinct batch_names. **One experiment** (`BNC001`, "BNC 001"), **one sample
per distinct batch_name**. The single empty batch_name maps to
`sample.code = 'unnamed'` (no biological identity to preserve). The trailing
`_CORRUPTED` marker on one file is preserved verbatim. Applied by
`link_bnc_samples.py` (18 files → 18 samples).

## MAB ("Antibody-antigen crosslinking")

3 files matching `71_s<N>_rep1_120ng`. Constant tokens (column `71_`,
`_rep1_120ng`) are stripped from sample codes. **One experiment**
(`MAB001`, "MAB 001"), 3 samples coded `s2`, `s3`, `s4` (1 file each).
Applied by `link_mab_samples.py`.

## QCX

QCX is an instrument-QC HeLa stream: 64 files across 34 dates, only 16
distinct `batch_name` values (the same QC sample re-runs periodically).
Modelled as **one experiment** (`QCX001`, "QCX 001") with **one sample per
distinct batch_name** — when a batch_name reappears on a later date, the
new file links to the existing sample. `sample.code = batch_name` verbatim;
`sample.name = batch_name.replace('_', ' ')`; `crosslinked_sample = 0`.
Applied by `link_qcx_samples.py` (64 files → 16 samples).

## AK2

AK2 batch_names use the leading token as a *condition*, not an experiment
code, and have no `S<plate>-<well>_1_<run>` tail. Files are routed into 3
experiments by first-token prefix:

| Prefix(es)            | Experiment     | Notes                                  |
|-----------------------|----------------|----------------------------------------|
| `EV_`, `AK2_`         | `AK2001`       | Paired conditions, same `_<conc>_<crosslinker>_<rep>_47_DIA` tail. Merged per user decision. |
| `FLAG-AK2_`           | `AK2002`       | FLAG-AK2 pulldown (PD) experiments.    |
| `10mM_`               | `AK2003`       | 10mM SDA fractionated runs.            |

Within each experiment: **one sample per distinct `batch_name`** (verbatim).
`sample.code = batch_name`, `sample.name = batch_name.replace('_', ' ')`,
`crosslinked_sample = 0`. No deduplication, no merging of replicates /
fractions / DIA-DDA variants. Applied by `link_ak2_samples.py`
(247 files → 139 samples across 3 experiments).
