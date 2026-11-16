# Snooping Cache Coherence Simulator

A small Python program that simulates a bus-based shared-memory multiprocessor
(1–3 processors, 1–3 variables) and shows, step by step, how each snooping
protocol keeps the caches coherent.

## Supported protocols

| Name to use in input / CLI | Protocol | States |
|---|---|---|
| `WRITE_INVALIDATE_WRITE_THROUGH` | Write-Invalidate, Write-Through | VALID, INV |
| `WRITE_INVALIDATE_WRITE_BACK` | Write-Invalidate, Write-Back (Ownership) | RO, RW, INV |
| `WRITE_ONCE` | Goodman's Write-Once | VALID, RES, DIRTY, INV |
| `WRITE_UPDATE_PARTIAL_WRITE_THROUGH` | Write-Update, Partial Write-Through (bonus) | V-EXCL, SHARED, DIRTY |
| `WRITE_UPDATE_PARTIAL_WRITE_BACK` | Write-Update, Partial Write-Back (bonus) | V-EXCL, S-CLEAN, S-DIRTY, DIRTY |

## Requirements

- Python 3.8 or newer
- No external libraries (standard library only)

## How to run

```bash
# run the protocol written in the input file
python simulator.py tests/benchmark1.txt

# pick a protocol from the command line (overrides the PROTOCOL line)
python simulator.py tests/benchmark1.txt --protocol WRITE_ONCE

# run all five protocols one after another
python simulator.py tests/benchmark1.txt --protocol ALL

# save the report to a file instead of printing it
python simulator.py tests/benchmark2.txt -p ALL -o result.txt

# read the input from stdin
python simulator.py < tests/benchmark1.txt
```

### Command-line options

| Option | Meaning |
|---|---|
| `input` | Input file. If left out, the program reads from stdin. |
| `-p`, `--protocol NAME` | Protocol to run (see table above), or `ALL`. Overrides the `PROTOCOL:` line. |
| `-o`, `--output FILE` | Write the report to `FILE` instead of the screen. |
| `-h`, `--help` | Show help. |

## Input format

```text
# Lines starting with # are comments; blank lines are ignored.
PROTOCOL: WRITE_INVALIDATE_WRITE_BACK
MEMORY: X=5, Y=10, Z=20
PROCESSORS: P1, P2, P3
P1: Read(X)
P2: Write(X, 10)
P1: Replace(X)
```

- `MEMORY` – 1 to 3 variables with their starting values.
- `PROCESSORS` – 1 to 3 processor names (any names, e.g. `P, Q` or `P1, P2, P3`).
- Operations – `Read(Var)`, `Write(Var, Value)`, `Replace(Var)`, one per line.
  Keywords are not case-sensitive.

The program stops with a clear message if something is wrong
(unknown processor or variable, missing write value, too many variables, ...).

## Output

1. **Trace table** – one row per operation: serial number, event (labelled
   Read-Hit / Read-Miss / Write-Hit / Write-Miss / Replacement), memory values,
   and the value + state of every variable in every cache. `-` means the
   block is not in that cache.
2. **Final summary** – final memory and cache contents, plus:
   read hits/misses, write hits/misses, replacements, bus invalidations,
   bus update broadcasts, memory write-backs and memory write-throughs.

## Project structure

```text
coherence-sim/
├── simulator.py     # input parsing, simulation loop, trace + summary printing, CLI
├── hardware.py      # CacheBlock, Memory, Cache, Bus, Stats
├── protocols.py     # the five coherence protocols
└── tests/
    ├── benchmark1.txt          # Benchmark 1 (P, Q, X=5)
    ├── benchmark2.txt          # Benchmark 2 (P1–P3, X=10, Z=80)
    ├── benchmark1_output.txt   # saved output, all protocols
    └── benchmark2_output.txt
```

**Design:** the cache and the bus don't contain any coherence rules. Whenever a
cache reads, writes, or snoops a bus transaction, it asks the protocol object
what to do. Adding a new protocol means writing one new class in
`protocols.py`.

## Assumptions

These are the cases where the assignment text leaves room for interpretation:

1. **A block in INV counts as a miss**, same as a block that isn't cached
   (e.g. Benchmark 1, step 6 is a Write-Miss).
2. **Owner caches flush to memory only on a read-miss.** When another cache
   *reads* a block held in RW / DIRTY, the owner supplies the data and also
   updates memory. On a *write-miss* the owner just hands the data over and is
   invalidated, without updating memory, because the new writer is about to
   change the value anyway. (Benchmark 1, step 6: memory stays 5 in
   Write-Back and 10 in Write-Once — confirmed by textbook Tables 4.6 and 4.8.)

## Verification

Benchmark 1 output matches textbook Tables 4.4, 4.6 and 4.8 on every row
(memory, cache values and states). See `Verification_Report.pdf` for the full traces.
3. **Write-Once read-miss on a RES block:** the RES copy drops to VALID,
   since it is no longer the only copy.
4. **Replace** removes the block from the cache (shown as `-`). Blocks in
   RW, DIRTY or S-DIRTY are written back first. Replacing a block that isn't
   cached does nothing and is labelled "Replacement, not cached".
5. **Counting:** each INVALID broadcast counts as one bus invalidation, no
   matter how many caches it hits. A write-miss in an invalidate protocol
   sends one INVALID. Write-throughs and write-backs are counted separately.
6. **Write-update protocols:** writing a block that no other cache holds stays
   local (→ DIRTY); writing a shared block broadcasts an update.
