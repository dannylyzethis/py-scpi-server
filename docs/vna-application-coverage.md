# VNA application coverage contract

Every application advertised by the VNA capability profile is classified as a measurement,
modifier, stimulus, or analysis behavior in the runtime application contract. Each contract
names a real option-gated command registered by the instrument.

The repository test suite compares that contract directly with the packaged capability
profile and live typed command registry. Adding an application to the catalog without adding
behavior and an option gate therefore fails CI instead of silently exposing metadata-only
functionality.

The individual application guides describe their command surfaces and scenario stream names.
These are project-owned behavioral contracts for automation development, not reproductions of
commercial instrument internals.
