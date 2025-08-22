# Changelog

All notable changes to the SCPI Equipment Emulator project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

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
  - Previously validation was lost when LabVIEW/NI-MAX performed device clear
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
- **MAJOR**: Full LabVIEW/NI-MAX compatibility
- Comprehensive IEEE 488.2 command support
  - `*CLS`, `*ESE`, `*ESR?`, `*IDN?`, `*OPC`, `*OPC?`
  - `*RST`, `*SRE`, `*STB?`, `*TST?`, `*WAI`
  - `SYST:ERR?`, `SYST:VERS?`
- VISA Device Clear simulation
- Proper SCPI error queue management

### Removed - BREAKING CHANGE
- **Welcome message removed** for full VISA compatibility
  - Previous versions sent welcome message on connection
  - LabVIEW/NI-MAX expects clean communication without welcome
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
- LabVIEW VISA compatibility
- Pure Python implementation (no dependencies)

### Core Features
- Single instrument emulation
- Basic command-response pairs
- TCP/IP communication
- VISA resource string support

### Supported Platforms
- Python 3.6+
- Windows, macOS, Linux
- LabVIEW 2018+

---

## Version Support

| Version | Support Status | Python | LabVIEW | Notes |
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