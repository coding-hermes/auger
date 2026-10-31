# Auger spec assembly — 4 block(s)

selector: kind=requirement, applies-to=auger.export, status=active
matched: 2 · dependencies: 2 · total: 4
schema: 1.0

## AUG-SHARED-DETERMINISM · requirement · active · v1.1.0
- source: shared.xml#AUG-SHARED-DETERMINISM
- applies-to: auger.export auger.spec
- verification: analysis
- selection: matched

Identical inputs shall produce byte-identical assembled output; assembly reads the corpus and writes nothing back to it.

## AUG-REQ-014 · requirement · active · v1.0.0
- source: export.xml#AUG-REQ-014
- applies-to: auger.export
- verification: test
- depends-on: AUG-SHARED-DETERMINISM
- selection: matched

The default `auger export` output remains deterministic and read-only.

## AUG-AC-014-1 · acceptance · active · v1.0.0
- source: export.xml#AUG-AC-014-1
- applies-to: auger.export
- verification: test
- verifies: AUG-REQ-014
- selection: dependency (via AUG-REQ-014 verifies)

Running the export twice over the same stored rows produces byte-identical output.

## AUG-VER-014-1 · verification · active · v1.0.0
- source: export.xml#AUG-VER-014-1
- applies-to: auger.export
- method: test
- verifies: AUG-REQ-014
- selection: dependency (via AUG-REQ-014 verifies)

Golden Markdown fixture compared against render_export output, twice, byte for byte.
