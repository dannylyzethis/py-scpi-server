# Changelog

All notable changes to the SCPI Equipment Emulator project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [4.0.0] - 2026-08-26

### Added

- Bench schema version 2 with an optional validated `reported_model` identity override.
- Stateful one-, two-, three-, and four-output generic power-supply profiles.
- Generic static CSV identities for DMM Type A/B, Oscilloscope Type A/B, a one-output supply,
  a signal generator, and topology-described VNA profiles.

### Changed

- Replaced all repository-owned instrument model identifiers with project-owned generic profile
  selectors and readable virtual identities.
- Separated catalog behavior selection from the model text returned by `*IDN?`.
- Removed prior built-in selector aliases; version-1 bench files must be rewritten as schema 2.

## [3.0.0] - 2026-08-25

### Added

- Interactive `load bench`, `bench`, and `instruments` commands with transactional composition,
  stopped/running inspection, serials, and VISA resource strings through one active runtime.
- Event-driven dashboard updates for commands on every transport, instrument state, scenarios,
  clients, and server lifecycle, with coalescing, UI-state preservation, and fallback polling.
- Interactive catalog browsing for drivers and models, including driver-owned configuration-field
  metadata and optional CSV-directory discovery.
- A guided interactive bench builder with catalog choices, safe instance/serial/port defaults,
  mixed built-in/CSV composition, all-capability defaults, validation, preview, and atomic
  schema-version-1 JSON saving.
- A common per-instance `serial_number` bench field applied consistently to built-in and CSV
  instrument identity responses.
- A catalog-visible `virtual-ps` driver with three independent selected-output state
  objects, protection/range settings, measurements, and correct `*CLS`/`*RST` separation.
- A mixed bench example containing two same-model supplies with unique serials and one CSV-defined
  fixture controller.
- A consolidated user-facing instrument catalog with every driver, model, configuration field,
  default, semantic hardware feature, application ID, serial rule, and copyable bench object.
- VNA `frequency_minimum_hz` and `frequency_maximum_hz` bench configuration fields that define
  emulated instrument, sweep, and trace limits without a product-model ceiling.

### Changed

- Replaced vendor-derived VNA family identities with `vna-2-port` and `vna-4-port`.
- Replaced numbered hardware configurations and coded options with `source_count`, semantic
  `hardware_features`, and semantic `applications`; omitted selections enable all compatible
  capabilities.
- Renamed VNA internals, manifests, coverage reports, tools, tests, CSV fixtures, and documentation
  to the project-owned vocabulary with no legacy aliases.
- Interactive and long-running CLI status output now uses console-safe text so redirected Windows
  processes do not terminate with a code-page `UnicodeEncodeError`.
- The legacy single-context PSU block in `detailed_instruments.csv` is now labeled generically
  instead of claiming to be a stateful built-in power-supply profile.
- ZIP installation documentation now explains environment activation and stale executable detection.

## [2.4.0] - 2026-08-25

### Added

- Stateful VNA channels, measurements, selected context, display windows and traces, formats, math
  memory, markers, limits, and equations, including indexed and abbreviated CALC/DISP commands.
- VNA measurement reset semantics that preserve configuration across `*CLS` and Device Clear while
  restoring a coherent channel-1/S11/window-1 preset on `*RST`.
- Expanded model-specific VNA coverage reports from 64 to 154 implemented documented commands.
- Coherent VNA linear, logarithmic, CW, power, and segmented sweep configuration with frequency
  axes, source power, receiver attenuation, IF bandwidth, dwell, and acquisition-derived timing.
- VNA adapters over the shared deterministic scenario engine for SDATA, FDATA, RDATA, receiver,
  SNP, and X-axis queries with ASCII/binary encoding and trigger/operation playback policies.
- A built-in Virtual DMM driver and shared scalar adapter for READ, FETCH, MEASURE,
  function/range configuration, queued values, trigger/operation policies, errors, and reset.
- VXI-11 Revision 1.0 `INSTR` transport with bounded ONC RPC/XDR framing, TCP portmapping, link and
  lock ownership, chunked writes/reads, Device Clear, bus trigger, abort, serial poll, and
  OPC-driven asynchronous SRQ callbacks, verified with PyVISA-Py and native VISA clients.
- A local-only-by-default dashboard control plane with required authentication for remote binds,
  CSRF enforcement, same-origin WebSockets, serialized instrument mutations, input bounds, security
  headers, and text-safe rendering of instrument command data.
- Hardened raw-SCPI socket transport on the standard port 5025 with bounded binary-aware framing,
  configurable termination, one active session per instrument, timeouts, backpressure, and clean
  shutdown behavior.
- Versioned virtual-bench definitions, JSON load/save, catalog-backed transactional composition,
  resource rendering, deployment-host overrides, and rollback-safe multi-server startup.
- A generic deterministic scenario engine with queued scalar/trace/table/event/error streams,
  explicit advancement and exhaustion policies, observable timing/playback, and seeded reset.
- Versioned JSON and compressed-binary scenario codecs with complex and typed binary-vector values.
- An immutable instrument-driver metadata contract, catalog, model lookup, factory API, and
  `scpi_emulator.drivers` entry-point discovery for external emulator families.
- A built-in VNA catalog driver derived from a versioned capability snapshot.
- Capability-gated VNA application profiles for strict and broad development testing.
- Coherent developer profiles that select capable modeled hardware and compatible application
  licenses while preserving truthful identity, option, and hardware queries.
- VNA application capability names wired into typed command availability gates.

### Planned
- Advanced SCPI subsystem support
- Binary data transfer for waveforms
- Service Request (SRQ) simulation
- Docker containerization
- Cloud deployment templates

## [2.3.0] - 2025-01-XX

### Added - Web Dashboard Release
- **Web Dashboard**: Real-time monitoring and control interface
  - Live command/response tracking with WebSocket updates
  - System metrics and performance monitoring
  - Remote instrument control capabilities
  - Configuration file upload via web interface
  - Modern responsive UI with real-time updates
- **REST API**: HTTP endpoints for external integration
  - `/api/status` - System and instrument status
  - `/api/commands` - Recent command history
  - `/api/start_all` - Start all instruments
  - `/api/stop_all` - Stop all instruments
  - `/api/restart/<id>` - Restart specific instrument
  - `/api/send_command/<id>` - Send commands remotely
- **WebSocket Integration**: Real-time command streaming
- **Flask Framework**: Professional web application architecture
- **Command Logging**: Centralized command/response tracking
- **Performance Metrics**: Commands per minute, uptime tracking

### Changed
- Server architecture enhanced to support web dashboard integration
- Command processing now includes real-time logging for web interface
- Error handling improved for web API responses

### Dependencies
- **Optional**: Flask and Flask-SocketIO for web dashboard functionality
- Maintains backward compatibility with pure Python operation

### Technical Details
- Web dashboard runs on port 8081 by default
- WebSocket communication for real-time updates
- Thread-safe command logging with circular buffer
- Modern HTML5/CSS3/JavaScript frontend with responsive design

## [2.2.0] - 2025-01-XX

### Fixed - Validation Preservation
- **Critical Fix**: Validation rules now survive VISA device clear operations
  - Previously validation was lost when an ATE client or VISA console performed device clear
  - Now stores validation rules separately from command handlers
  - Ensures consistent behavior across reconnections
- **Enhanced Debugging**: Improved validation tracking and error reporting
  - Added detailed logging for validation rule storage and retrieval
  - Better error messages for validation failures
  - Enhanced debugging output for stateful command linking

### Changed
- Stateful command behavior improved across reconnections
- Validation debugging enhanced with detailed logging
- Error reporting more descriptive for validation failures

### Technical Details
- `validation_rules` dictionary stores validation separately from commands
- `default_values` dictionary preserves query defaults across device clear
- Enhanced `link_stateful_commands()` method with validation preservation
- Improved `_create_stateful_set()` with persistent validation support

## [2.1.0] - 2025-01-XX

### Added - Enhanced Validation
- Enhanced range validation extraction and processing
- Improved validation tracking in stateful commands
- Better logging for debugging validation issues

### Fixed
- Range validation extraction from command handlers
- Validation preservation during stateful command linking
- Command handler closure issues with validation parameters

### Improved
- Debug logging for validation processing
- Error messages for validation failures
- Stateful command state tracking

## [2.0.0] - 2025-01-XX

### Added - VISA Compatibility
- **MAJOR**: Full ATE client and VISA console compatibility
- Comprehensive IEEE 488.2 command support
  - `*CLS`, `*ESE`, `*ESR?`, `*IDN?`, `*OPC`, `*OPC?`
  - `*RST`, `*SRE`, `*STB?`, `*TST?`, `*WAI`
  - `SYST:ERR?`, `SYST:VERS?`
- VISA Device Clear simulation
- Proper SCPI error queue management

### Removed - BREAKING CHANGE
- **Welcome message removed** for full VISA compatibility
  - Previous versions sent welcome message on connection
  - ATE clients and VISA consoles expect clean communication without a welcome message
  - This is a breaking change for clients expecting welcome message

### Fixed
- Lambda closure issues in command handlers
- Unicode handling improvements
- Connection state management

### Technical Details
- `visa_device_clear()` method simulates proper VISA behavior
- Error queue implements SCPI-compliant error format
- State management improved for multi-client scenarios

## [1.3.0] - 2024-XX-XX

### Added
- Excel file support with openpyxl integration
- Automatic delimiter detection for CSV files
- Enhanced error handling and logging

### Improved
- File reading robustness
- Configuration validation
- Error reporting

## [1.2.0] - 2024-XX-XX

### Added
- Input validation system
  - Range validation (`range:min,max`)
  - Enumeration validation (`enum:val1,val2,val3`)
  - Boolean validation (`bool`)
- Stateful command support (SET/QUERY pairs)
- Enhanced command processing with parameterized responses

### Improved
- Command parsing with regex support
- State management for instruments
- Validation error handling

## [1.1.0] - 2024-XX-XX

### Added
- Multiple instrument support
- Port configuration in CSV
- Interactive command-line interface
- Comprehensive logging system

### Improved
- TCP server stability
- Error handling
- Documentation

## [1.0.0] - 2024-XX-XX

### Added - Initial Release
- Basic SCPI command emulation
- CSV configuration file support
- TCP server implementation
- VISA-based ATE client compatibility
- Pure Python implementation (no dependencies)

### Core Features
- Single instrument emulation
- Basic command-response pairs
- TCP/IP communication
- VISA resource string support

### Supported Platforms
- Python 3.6+
- Windows, macOS, Linux
- VISA-capable ATE clients

---

## Version Support

| Version | Support Status | Python | ATE clients | Notes |
|---------|---------------|--------|---------|-------|
| 2.3.x   | ✅ Active     | 3.6+   | 2018+   | Current with web dashboard |
| 2.2.x   | ✅ Active     | 3.6+   | 2018+   | Validation fixes |
| 2.1.x   | ⚠️ Maintenance | 3.6+   | 2018+   | Critical fixes only |
| 2.0.x   | ⚠️ Maintenance | 3.6+   | 2018+   | Critical fixes only |
| 1.x.x   | ❌ End of Life | 3.6+   | 2018+   | No longer supported |

## Migration Guide

### From 2.2.x to 2.3.x
- **Optional**: Install Flask dependencies for web dashboard
- **New**: Web dashboard available at http://localhost:8081
- **Backward Compatible**: All existing configurations work unchanged

### From 2.1.x to 2.2.x
- **Fully Compatible**: No breaking changes
- **Improved**: Validation now survives VISA device clear
- **Enhanced**: Better debugging and error reporting

### From 2.0.x to 2.1.x
- **Fully Compatible**: No breaking changes
- **Improved**: Enhanced validation processing

### From 1.x.x to 2.0.x
- **Breaking**: Remove any code expecting welcome message
- **Added**: Full IEEE 488.2 command support
- **Enhanced**: VISA compatibility improvements

## Contributors

### Version 2.3.0
- Enhanced web dashboard development
- Real-time monitoring implementation
- WebSocket integration

### Version 2.2.0
- Critical validation preservation fixes
- Enhanced debugging capabilities

### Version 2.1.0
- Validation system improvements
- Enhanced logging implementation

### Version 2.0.0
- VISA compatibility implementation
- IEEE 488.2 standard compliance
- Major architecture improvements

---

For detailed technical information about each version, see the individual release notes and documentation.
