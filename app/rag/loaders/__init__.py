"""Document loaders: one module per supported file format.

Each loader implements the `app.rag.blocks.DocumentLoader` protocol —
`load(path: Path) -> list[Block]` — and raises `app.rag.blocks.LoaderError`
when a document cannot be parsed into blocks.
"""
