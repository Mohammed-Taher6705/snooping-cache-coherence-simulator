"""
hardware.py - the pieces of our little multiprocessor.

We model four things:
    CacheBlock  - one variable living inside a cache (value + state)
    Memory      - the shared global memory
    Cache       - the private cache of one processor
    Bus         - the shared wire everyone listens to ("snoops")

The caches and the bus don't know any coherence rules themselves.
Whenever a decision is needed, they ask the protocol object (see protocols.py).
"""

INV = "INV"   # every protocol uses INV for "this copy is useless"


class CacheBlock:
    def __init__(self, var, value, state):
        self.var = var
        self.value = value
        self.state = state


class Memory:
    def __init__(self, initial_values):
        # e.g. {"X": 5, "Y": 10}; dicts remember insertion order, so X,Y,Z stay in order
        self.values = dict(initial_values)

    def read(self, var):
        return self.values[var]

    def write(self, var, value):
        self.values[var] = value


class Stats:
    """Counters we print at the end of the run."""
    def __init__(self):
        self.read_hits = 0
        self.read_misses = 0
        self.write_hits = 0
        self.write_misses = 0
        self.replacements = 0
        self.invalidations = 0    # INVALID commands sent on the bus
        self.updates = 0          # update broadcasts (write-update protocols)
        self.write_backs = 0      # a cache flushed its newer copy to memory
        self.write_throughs = 0   # a write went straight through to memory


class Cache:
    def __init__(self, name, bus):
        self.name = name
        self.bus = bus
        self.blocks = {}          # var -> CacheBlock
        bus.caches.append(self)

    def get(self, var):
        """Return the block only if it is usable (present and not INV)."""
        block = self.blocks.get(var)
        if block is None or block.state == INV:
            return None
        return block

    def load(self, var, value, state):
        self.blocks[var] = CacheBlock(var, value, state)

    def drop(self, var):
        self.blocks.pop(var, None)


class Bus:
    """
    The shared bus. Anything sent here is seen by every other cache at once.
    Each method below is one kind of bus transaction.
    """

    def __init__(self, memory, protocol, stats):
        self.memory = memory
        self.protocol = protocol
        self.stats = stats
        self.caches = []

    def others_with_copy(self, var, me):
        """Caches (other than me) that still hold a usable copy of var."""
        return [c for c in self.caches if c is not me and c.get(var)]

    def fetch(self, var, me, for_write=False):
        """
        A cache missed and asks for the block.
        Other caches snoop the request; an owner cache may answer instead of memory.
        Returns (value, shared) where shared tells if someone else also has a copy.
        """
        value = None
        for other in self.others_with_copy(var, me):
            block = other.get(var)
            supplies, flush = self.protocol.snoop_fetch(block, for_write)
            if supplies:
                value = block.value
                if flush:
                    self.write_back(var, block.value)

        if value is None:                     # no owner answered -> memory answers
            value = self.memory.read(var)
        shared = len(self.others_with_copy(var, me)) > 0
        return value, shared

    def invalidate(self, var, me):
        """Broadcast INVALID: every other copy of var becomes useless."""
        self.stats.invalidations += 1
        for other in self.others_with_copy(var, me):
            other.get(var).state = INV

    def update(self, var, value, me, also_memory):
        """Broadcast the new value so other caches can refresh their copies."""
        self.stats.updates += 1
        for other in self.others_with_copy(var, me):
            self.protocol.snoop_update(other.get(var), value)
        if also_memory:
            self.write_through(var, value)

    def write_back(self, var, value):
        self.memory.write(var, value)
        self.stats.write_backs += 1

    def write_through(self, var, value):
        self.memory.write(var, value)
        self.stats.write_throughs += 1
