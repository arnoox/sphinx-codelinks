Strict schema with populated sphinx-codelinks fields
======================================================

This need is produced by the ``src-trace`` directive from a source comment,
with ``set_local_url``/``set_remote_url`` enabled. Unlike a plain manually
written need, its ``local-url``/``remote-url`` fields are actually populated
(non-``None``), so they are not stripped before strict schema validation and
must be declared in the schema to avoid an unevaluated-properties violation.

.. src-trace::
   :project: demo
   :file: demo.cpp
