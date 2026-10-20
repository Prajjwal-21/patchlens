# Demo service

A small, realistic Python service used by PatchLens' local demo workflow:

```bash
make demo-repo   # register + index this checkout
make demo-live   # review pr.diff against the index
```

It contains arithmetic helpers, a price table, a thin HTTP handler layer and a
partially complete test suite, so retrieval has mirrored test files, call edges
and docstrings to work with.
