# VNA application coverage contract

The repository tracks two different kinds of coverage. They answer different questions and must
not be combined into a single percentage:

- `vna_commands.v1.json` is the project-owned core command contract. Its reports show whether every
  command the project documented is registered. A result such as `393/393` does **not** mean every
  command used by every VNA application has been audited.
- `vna_application_compatibility.v1.json` is the application syntax inventory. It lists every
  advertised application and the command forms reviewed for that application. Its reports compare
  those forms with the live typed registry.

The application reports are:

- `reports/vna-application-coverage-vna-2-port.json`
- `reports/vna-application-coverage-vna-4-port.json`

All 25 advertised applications are audited: gain compression, active hot parameters, noise figure,
frequency offset, scalar mixer, frequency converter, embedded LO, basic and integrated pulsed RF,
fast CW, arbitrary waveform generation, source phase control, true-mode stimulus, base and enhanced
time domain, fixture removal, spectrum analysis, intermodulation distortion, modulation distortion,
phase noise, differential I/Q, wideband I/Q, measurement uncertainty, N-port, and performance test.
All 731 inventoried forms are implemented on both virtual models, with zero pending applications and
zero registry gaps. CI verifies that every completed inventory has no missing commands and that the
checked-in reports match the live registry. The release-quality profile invokes
`tools/vna_application_coverage.py` for both models without `--allow-pending`, so either a pending
application or a missing command is a hard CI failure rather than an informational checkpoint.

The inventory contains normalized command syntax and project-written behavioral metadata only. It
does not reproduce vendor manual prose or model/option identifiers.
