"""
simulator.py - bus-based snooping cache coherence simulator.

How to run:
    python simulator.py input.txt
    python simulator.py input.txt --protocol WRITE_ONCE
    python simulator.py input.txt --protocol ALL        (runs every protocol)
    python simulator.py < input.txt                     (read from stdin)

Input file example:
    PROTOCOL: WRITE_INVALIDATE_WRITE_BACK
    MEMORY: X=5, Y=10, Z=20
    PROCESSORS: P1, P2, P3
    P1: Read(X)
    P2: Write(X, 10)
    P1: Replace(X)
"""

import argparse
import re
import sys

from hardware import Bus, Cache, Memory, Stats
from protocols import ALL_PROTOCOLS


# ---------------------------------------------------------------------------
# Reading the input
# ---------------------------------------------------------------------------
OPERATION = re.compile(r"(\w+)\s*:\s*(read|write|replace)\s*\(\s*(\w+)\s*(?:,\s*(-?\d+)\s*)?\)",
                       re.IGNORECASE)


def parse_input(text):
    protocol, memory, processors, operations = None, {}, [], []

    for line_no, line in enumerate(text.splitlines(), 1):
        line = line.split("#")[0].strip()           # drop comments
        if not line:
            continue
        key = line.split(":")[0].strip().upper()
        rest = line.split(":", 1)[1] if ":" in line else ""

        if key == "PROTOCOL":
            protocol = rest.strip().upper()
        elif key == "MEMORY":
            for item in rest.split(","):
                name, value = item.split("=")
                memory[name.strip()] = int(value)
        elif key == "PROCESSORS":
            processors = [p.strip() for p in rest.split(",") if p.strip()]
        else:
            match = OPERATION.fullmatch(line)
            if not match:
                sys.exit(f"Line {line_no}: I don't understand '{line}'")
            proc, op, var, value = match.groups()
            op = op.capitalize()
            if op == "Write" and value is None:
                sys.exit(f"Line {line_no}: Write needs a value, e.g. Write(X, 10)")
            operations.append((proc, op, var, int(value) if value else None))

    # sanity checks
    if not 1 <= len(memory) <= 3:
        sys.exit("MEMORY must have 1 to 3 variables")
    if not 1 <= len(processors) <= 3:
        sys.exit("PROCESSORS must list 1 to 3 processors")
    for proc, op, var, _ in operations:
        if proc not in processors:
            sys.exit(f"Unknown processor '{proc}' (not in PROCESSORS line)")
        if var not in memory:
            sys.exit(f"Unknown variable '{var}' (not in MEMORY line)")

    return protocol, memory, processors, operations


# ---------------------------------------------------------------------------
# The simulator itself
# ---------------------------------------------------------------------------
class Simulator:
    def __init__(self, protocol_name, memory, processors):
        self.protocol = ALL_PROTOCOLS[protocol_name]()
        self.stats = Stats()
        self.memory = Memory(memory)
        self.bus = Bus(self.memory, self.protocol, self.stats)
        self.caches = {name: Cache(name, self.bus) for name in processors}
        self.trace = []                                # list of (event, snapshot)
        self.record("Initial State")

    def step(self, proc, op, var, value):
        cache = self.caches[proc]
        if op == "Read":
            result = self.protocol.read(cache, var)
            text = f"{proc} reads {var}"
        elif op == "Write":
            result = self.protocol.write(cache, var, value)
            text = f"{proc} updates {var}={value}"
        else:
            result = self.protocol.replace(cache, var)
            text = f"{proc} replaces {var}"
        self.count(result)
        self.record(f"{text} ({result})")

    def count(self, result):
        s = self.stats
        if result == "Read-Hit":     s.read_hits += 1
        elif result == "Read-Miss":  s.read_misses += 1
        elif result == "Write-Hit":  s.write_hits += 1
        elif result == "Write-Miss": s.write_misses += 1
        else:                        s.replacements += 1

    def record(self, event):
        """Take a picture of memory and all caches after this step."""
        mem = [str(v) for v in self.memory.values.values()]
        caches = []
        for cache in self.caches.values():
            cells = []
            for var in self.memory.values:
                block = cache.blocks.get(var)
                cells += [str(block.value), block.state] if block else ["-", "-"]
            caches.append(cells)
        self.trace.append((event, mem, caches))

    # ------------------------------------------------------------------ output
    def trace_table(self):
        variables = list(self.memory.values)
        mem_head = variables
        cache_head = [h for v in variables for h in (f"Val({v})", f"State({v})")]

        # width of every small column = longest thing that will appear in it
        def widths(header, column_values):
            return [max(len(h), *(len(x) for x in col)) for h, col in zip(header, column_values)]

        mem_w = widths(mem_head, zip(*[t[1] for t in self.trace]))
        all_cache_rows = [c for t in self.trace for c in t[2]]
        cache_w = widths(cache_head, zip(*all_cache_rows))
        event_w = max(len("Event"), *(len(t[0]) for t in self.trace))

        def cells(values, w):
            return "  ".join(v.ljust(n) for v, n in zip(values, w))

        mem_group = max(len("Memory"), len(cells(mem_head, mem_w)))
        cache_group = max(len(cells(cache_head, cache_w)), max(len(p) for p in self.caches) + 6)

        def line(serial, event, mem, caches):
            parts = [serial.ljust(6), event.ljust(event_w), mem.ljust(mem_group)]
            parts += [c.ljust(cache_group) for c in caches]
            return " | ".join(parts).rstrip()

        title = line("Serial", "Event", "Memory", [f"{p} Cache" for p in self.caches])
        sub = line("", "", cells(mem_head, mem_w), [cells(cache_head, cache_w)] * len(self.caches))
        bar = "=" * len(sub)

        out = [bar, title, sub, bar]
        for i, (event, mem, caches) in enumerate(self.trace):
            out.append(line(str(i), event, cells(mem, mem_w), [cells(c, cache_w) for c in caches]))
        out.append(bar)
        return "\n".join(out)

    def summary(self):
        s = self.stats
        out = ["", "Final state", "-----------"]
        out.append("Memory : " + ", ".join(f"{v}={x}" for v, x in self.memory.values.items()))
        for name, cache in self.caches.items():
            parts = []
            for var in self.memory.values:
                b = cache.blocks.get(var)
                parts.append(f"{var}={b.value} ({b.state})" if b else f"{var}=-")
            out.append(f"{name:<7}: " + ", ".join(parts))
        out += ["", "Statistics", "----------",
                f"Read hits  / misses      : {s.read_hits} / {s.read_misses}",
                f"Write hits / misses      : {s.write_hits} / {s.write_misses}",
                f"Replacements             : {s.replacements}",
                f"Bus invalidations        : {s.invalidations}",
                f"Bus update broadcasts    : {s.updates}",
                f"Memory write-backs       : {s.write_backs}",
                f"Memory write-throughs    : {s.write_throughs}"]
        return "\n".join(out)

    def report(self):
        return f"PROTOCOL: {self.protocol.name}\n{self.trace_table()}\n{self.summary()}\n"


# ---------------------------------------------------------------------------
# Command line
# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(description="Snooping cache coherence simulator")
    parser.add_argument("input", nargs="?", help="input file (reads stdin if omitted)")
    parser.add_argument("-p", "--protocol", help="override the PROTOCOL line, or ALL")
    parser.add_argument("-o", "--output", help="save the report to a file")
    args = parser.parse_args()

    text = open(args.input).read() if args.input else sys.stdin.read()
    protocol, memory, processors, operations = parse_input(text)

    chosen = (args.protocol or protocol or "").upper()
    names = list(ALL_PROTOCOLS) if chosen == "ALL" else [chosen]
    for name in names:
        if name not in ALL_PROTOCOLS:
            sys.exit(f"Unknown protocol '{name}'. Choose one of: {', '.join(ALL_PROTOCOLS)}, ALL")

    reports = []
    for name in names:
        sim = Simulator(name, memory, processors)
        for op in operations:
            sim.step(*op)
        reports.append(sim.report())

    result = "\n\n".join(reports)
    if args.output:
        with open(args.output, "w") as f:
            f.write(result)
        print(f"Report saved to {args.output}")
    else:
        print(result)


if __name__ == "__main__":
    main()
