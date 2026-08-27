# Library API and facade migration

The command-line entry point remains the simplest supported interface:

```powershell
scpi-emulator --help
```

Code embedding the emulator should import objects from the module that owns their behavior:

| Purpose | Supported import |
|---|---|
| Instrument core | `from scpi_emulator.instrument import SCPIInstrument` |
| Runtime manager | `from scpi_emulator.runtime import SCPIEmulatorManager` |
| Raw TCP server | `from scpi_emulator.raw_server import SCPIServer` |
| Dashboard | `from scpi_emulator.dashboard import WebDashboard` |
| CSV/XLSX loading | `from scpi_emulator.configuration import load_compatibility_path` |
| Configuration errors | `from scpi_emulator.configuration import ConfigurationError` |
| CLI parser or entry function | `from scpi_emulator.cli import build_parser, main` |

Earlier development snapshots temporarily exposed these names from
`scpi_emulator.emulator`. That facade was removed before the first tagged 4.0 release, so replace:

```python
from scpi_emulator.emulator import SCPIInstrument, SCPIEmulatorManager
```

with:

```python
from scpi_emulator.instrument import SCPIInstrument
from scpi_emulator.runtime import SCPIEmulatorManager
```

There is no runtime behavior change. The removal makes ownership explicit and prevents new code from
depending on a transitional module whose only purpose was re-exporting unrelated components.
