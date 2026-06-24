project = "schema-strictness-impl-demo"
copyright = "2026, useblocks"
author = "useblocks"

extensions = ["sphinx_needs", "sphinx_codelinks"]

exclude_patterns = ["_build", "Thumbs.db", ".DS_Store"]

# Same strict schema as ../schema_strictness, but here the src-trace directive
# produces a real "impl" need with set_local_url/set_remote_url enabled, so
# local-url/remote-url are populated (not None) and therefore not stripped
# before unevaluatedProperties validation.
needs_schema_definitions_from_json = "schemas.json"

src_trace_config_from_toml = "src_trace.toml"

html_theme = "alabaster"
