"""
protocols.py - the coherence rules.

Every protocol answers the same few questions:
    read(cache, var)            what happens when a processor reads
    write(cache, var, value)    what happens when a processor writes
    replace(cache, var)         what happens when a block is evicted
    snoop_fetch(block, ...)     how a cache reacts when someone else misses
    snoop_update(block, value)  how a cache reacts to an update broadcast

Each action returns a label for the trace: "Read-Hit", "Write-Miss", ...

A note on owners: an owner cache (RW / DIRTY) writes its data back to memory
when it serves a READ miss. On a WRITE miss it just hands the data over and
gets invalidated - the new writer is about to change the value anyway.
"""

from hardware import INV


class Protocol:
    name = ""
    dirty_states = ()    # states whose data is newer than memory (need write-back)

    # --- reading is the same idea everywhere, only the new state differs ---
    def read(self, cache, var):
        if cache.get(var):
            return "Read-Hit"
        value, shared = cache.bus.fetch(var, cache)
        cache.load(var, value, self.state_after_read_miss(shared))
        return "Read-Miss"

    def state_after_read_miss(self, shared):
        raise NotImplementedError

    # --- replacement: write back only if our copy is the newest one ---
    def replace(self, cache, var):
        block = cache.blocks.get(var)
        if block is None:
            return "Replacement, not cached"
        if block.state in self.dirty_states:
            cache.bus.write_back(var, block.value)
        cache.drop(var)
        return "Replacement"

    # --- default snoop behaviour: do nothing ---
    def snoop_fetch(self, block, for_write):
        return False, False          # (supplies data?, flush to memory?)

    def snoop_update(self, block, value):
        pass


# ===========================================================================
# 1) Write-Invalidate + Write-Through          states: VALID, INV
#    Memory is always up to date, so memory always serves misses.
# ===========================================================================
class WriteInvalidateWriteThrough(Protocol):
    name = "WRITE_INVALIDATE_WRITE_THROUGH"

    def state_after_read_miss(self, shared):
        return "VALID"

    def write(self, cache, var, value):
        bus = cache.bus
        block = cache.get(var)
        if block:                                  # write-hit
            block.value = value
            result = "Write-Hit"
        else:                                      # write-miss: bring the block in first
            bus.fetch(var, cache, for_write=True)
            cache.load(var, value, "VALID")
            result = "Write-Miss"
        bus.invalidate(var, cache)                 # kill the other copies
        bus.write_through(var, value)              # and keep memory current
        return result


# ===========================================================================
# 2) Write-Invalidate + Write-Back (Ownership)  states: RO, RW, INV
#    RW = I am the only owner, memory may be stale.
# ===========================================================================
class WriteInvalidateWriteBack(Protocol):
    name = "WRITE_INVALIDATE_WRITE_BACK"
    dirty_states = ("RW",)

    def state_after_read_miss(self, shared):
        return "RO"

    def write(self, cache, var, value):
        bus = cache.bus
        block = cache.get(var)
        if block:                                  # write-hit
            if block.state == "RO":                # need ownership first
                bus.invalidate(var, cache)
                block.state = "RW"
            block.value = value                    # RW: just write, no bus traffic
            return "Write-Hit"
        bus.fetch(var, cache, for_write=True)      # write-miss
        bus.invalidate(var, cache)
        cache.load(var, value, "RW")
        return "Write-Miss"

    def snoop_fetch(self, block, for_write):
        if block.state != "RW":
            return False, False                    # memory will answer
        if for_write:
            return True, False                     # hand it over, we get invalidated next
        block.state = "RO"                         # reader: share it and fix memory
        return True, True


# ===========================================================================
# 3) Goodman's Write-Once        states: VALID, RES, DIRTY, INV
#    First write goes through to memory (RES), later writes stay local (DIRTY).
# ===========================================================================
class WriteOnce(Protocol):
    name = "WRITE_ONCE"
    dirty_states = ("DIRTY",)

    def state_after_read_miss(self, shared):
        return "VALID"

    def write(self, cache, var, value):
        bus = cache.bus
        block = cache.get(var)
        if block:                                  # write-hit
            if block.state == "VALID":             # first write -> write-through
                bus.invalidate(var, cache)
                bus.write_through(var, value)
                block.state = "RES"
            else:                                  # RES or DIRTY -> write-back
                block.state = "DIRTY"
            block.value = value
            return "Write-Hit"
        bus.fetch(var, cache, for_write=True)      # write-miss
        bus.invalidate(var, cache)
        cache.load(var, value, "DIRTY")
        return "Write-Miss"

    def snoop_fetch(self, block, for_write):
        if block.state == "DIRTY":
            if for_write:
                return True, False
            block.state = "VALID"                  # supply data and update memory
            return True, True
        if block.state == "RES" and not for_write:
            block.state = "VALID"                  # no longer the only copy
        return False, False


# ===========================================================================
# 4) (Bonus) Write-Update + Partial Write-Through
#    states: V-EXCL (only copy, clean), SHARED (several clean copies),
#            DIRTY (only copy, memory stale)
#    Writing a shared block broadcasts the new value to the other caches
#    AND to memory. Writing a private block stays local.
# ===========================================================================
class WriteUpdatePartialWriteThrough(Protocol):
    name = "WRITE_UPDATE_PARTIAL_WRITE_THROUGH"
    dirty_states = ("DIRTY",)

    def state_after_read_miss(self, shared):
        return "SHARED" if shared else "V-EXCL"

    def write(self, cache, var, value):
        bus = cache.bus
        block = cache.get(var)
        if block:
            result = "Write-Hit"
        else:
            bus.fetch(var, cache, for_write=True)
            cache.load(var, value, "DIRTY")        # state fixed just below
            block = cache.get(var)
            result = "Write-Miss"

        block.value = value
        if bus.others_with_copy(var, cache):       # others hold it -> update them + memory
            bus.update(var, value, cache, also_memory=True)
            block.state = "SHARED"
        else:                                      # I'm alone -> write locally
            block.state = "DIRTY"
        return result

    def snoop_fetch(self, block, for_write):
        if block.state == "DIRTY":                 # supply and clean up memory
            block.state = "SHARED"
            return True, True
        block.state = "SHARED"                     # V-EXCL now has company
        return False, False

    def snoop_update(self, block, value):
        block.value = value                        # stay SHARED, just refresh


# ===========================================================================
# 5) (Bonus) Write-Update + Partial Write-Back
#    states: V-EXCL (only copy, clean), S-CLEAN (shared, not the owner),
#            S-DIRTY (shared, I'm the owner, memory stale),
#            DIRTY (only copy, memory stale)
#    Updates go to the other caches only; memory is fixed when the owner
#    finally replaces the block.
# ===========================================================================
class WriteUpdatePartialWriteBack(Protocol):
    name = "WRITE_UPDATE_PARTIAL_WRITE_BACK"
    dirty_states = ("DIRTY", "S-DIRTY")

    def state_after_read_miss(self, shared):
        return "S-CLEAN" if shared else "V-EXCL"

    def write(self, cache, var, value):
        bus = cache.bus
        block = cache.get(var)
        if block:
            result = "Write-Hit"
        else:
            bus.fetch(var, cache, for_write=True)
            cache.load(var, value, "DIRTY")
            block = cache.get(var)
            result = "Write-Miss"

        block.value = value
        if bus.others_with_copy(var, cache):       # update the other caches only
            bus.update(var, value, cache, also_memory=False)
            block.state = "S-DIRTY"                # I'm now the owner
        else:
            block.state = "DIRTY"
        return result

    def snoop_fetch(self, block, for_write):
        if block.state in ("DIRTY", "S-DIRTY"):    # owner supplies, keeps ownership
            block.state = "S-DIRTY"
            return True, False
        if block.state == "V-EXCL":
            block.state = "S-CLEAN"
        return False, False

    def snoop_update(self, block, value):
        block.value = value
        block.state = "S-CLEAN"                    # whoever wrote is the new owner


ALL_PROTOCOLS = {
    p.name: p for p in (
        WriteInvalidateWriteThrough,
        WriteInvalidateWriteBack,
        WriteOnce,
        WriteUpdatePartialWriteThrough,
        WriteUpdatePartialWriteBack,
    )
}
