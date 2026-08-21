# SCPI Equipment Emulator

[![Python Version](https://img.shields.io/badge/python-3.10+-blue.svg)](https://python.org)
[![License](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)
[![Version](https://img.shields.io/badge/version-0.1.0--alpha-brightgreen.svg)](https://github.com/dannylyzethis/py-scpi-server/releases)
[![LabVIEW Compatible](https://img.shields.io/badge/LabVIEW-Compatible-orange.svg)](https://www.ni.com/en-us/shop/labview.html)

A comprehensive SCPI (Standard Commands for Programmable Instruments) equipment emulator that provides virtual instrument functionality over TCP/IP. Perfect for testing LabVIEW applications, VISA drivers, and instrument automation without physical hardware.

## 🌟 Features

- **🔬 LabVIEW Compatible**: Full VISA TCP/IP support with proper device simulation
- **🌐 Web Dashboard**: Real-time monitoring and control interface (v2.3+)
- **📊 Multiple Instruments**: Run multiple virtual instruments simultaneously on different ports
- **📋 Excel/CSV Configuration**: Define instruments and commands using simple spreadsheets
- **⚡ IEEE 488.2 Compliance**: Built-in standard SCPI commands (`*IDN?`, `*RST`, etc.)
- **💾 Stateful Commands**: SET/QUERY command pairs with persistent state
- **✅ Input Validation**: Range, enum, and boolean validation with proper SCPI error handling
- **🔄 VISA Device Clear**: Proper simulation of VISA device clear operations
- **🚀 Minimal Core**: Python 3.10+ with no third-party dependency required for CSV profiles

## 📸 Screenshots

### Web Dashboard (v2.3)
![Web Dashboard](docs/images/dashboard.png)
*Real-time monitoring with live command tracking and system metrics*

### LabVIEW Integration
![LabVIEW Example](docs/images/labview-integration.png)
*Seamless integration with LabVIEW VISA drivers*

## 🚀 Quick Start

### Installation

```bash
# Clone the repository
git clone https://github.com/dannylyzethis/py-scpi-server.git
cd py-scpi-server

# Install the core emulator in editable mode
python -m pip install -e .

# Or include every optional runtime feature
python -m pip install -e ".[all]"
```

### Create Example Configuration

```bash
scpi-emulator --create-example
```

### Start Emulator

```bash
# Load configuration and start servers
scpi-emulator --load scpi_instruments_example.csv --start

# With web dashboard
scpi-emulator --load scpi_instruments_example.csv --start --web

# With verbose logging
scpi-emulator --load scpi_instruments_example.csv --start --web --verbose
```

### Connect from LabVIEW

The current transport is a raw TCP socket. Use these VISA resource strings in LabVIEW:
- **Keysight 34461A DMM**: `TCPIP0::localhost::5555::SOCKET`
- **Keysight E36312A PSU**: `TCPIP0::localhost::5556::SOCKET`
- **Tektronix TDS2024B Scope**: `TCPIP0::localhost::5557::SOCKET`
- **Agilent 33220A Generator**: `TCPIP0::localhost::5558::SOCKET`
- **Debug Test Instrument**: `TCPIP0::localhost::5559::SOCKET`

VXI-11/HiSLIP `::INSTR` compatibility is planned but not implemented yet.

## 📋 Configuration Format

Define your instruments in CSV or Excel format:

### CSV Example
```csv
Equipment,Port,Command,Response,Validation
My DMM,5555,*IDN?,MY_DMM,MODEL123,SERIAL456,1.0,
,,MEAS:VOLT:DC?,1.234567E+00,
,,VOLT (.+),OK,range:0,10
,,VOLT?,5.0,
```

### Excel Support
- Open the CSV in Excel and save as `.xlsx`
- Or install the Excel extra: `python -m pip install -e ".[excel]"`
- Supports `.xlsx`, `.xls`, and `.csv` formats

### Validation Rules

| Type | Format | Example | Description |
|------|--------|---------|-------------|
| **Range** | `range:min,max` | `range:0,10` | Validates numeric values within range |
| **Enum** | `enum:VAL1,VAL2,VAL3` | `enum:AC,DC,GND` | Validates against allowed values |
| **Boolean** | `bool` | `bool` | Accepts `ON/OFF/1/0` |

## 🔧 Command Line Options

```bash
scpi-emulator [OPTIONS]

Options:
  --load, -l FILE          Load instrument definitions (.csv, .xlsx, .xls)
  --start, -s              Start TCP servers immediately
  --web, -w                Start web dashboard (requires Flask)
  --web-port PORT          Web dashboard port (default: 8081)
  --port, -p PORT          Starting port number for instruments (default: 5555)
  --host HOST              Server host (default: localhost)
  --create-example         Create example CSV file
  --interactive, -i        Start interactive mode
  --verbose, -v            Enable verbose logging
  --log-file FILE          Also write logs to FILE
  --version                Show the package version
  --help                   Show help message
```

## 📡 SCPI Command Examples

### Basic Commands

```bash
# Connect via telnet for testing
telnet localhost 5555

# IEEE 488.2 Standard Commands
*IDN?                    # Get instrument identification
*RST                     # Reset instrument
*CLS                     # Clear status
SYST:ERR?               # Check error queue

# Instrument-specific commands
MEAS:VOLT:DC?           # Measure DC voltage
VOLT 5.0                # Set voltage to 5.0V
VOLT?                   # Query current voltage setting
```

### Validation Testing

```bash
# Connect to debug instrument
telnet localhost 5559

# Test range validation (valid range: 1-10)
TEST_RANGE 5            # ✅ Valid: returns "Range OK: 5" 
TEST_RANGE 15           # ❌ Invalid: adds error to queue
SYST:ERR?               # Check error: "-222,Data out of range..."

# Test enum validation
TEST_ENUM B             # ✅ Valid: accepts A, B, or C
TEST_ENUM X             # ❌ Invalid: not in enum list

# Test boolean validation  
TEST_BOOL ON            # ✅ Valid: accepts ON/OFF/1/0
TEST_BOOL MAYBE         # ❌ Invalid: not a boolean
```

## 🌐 Web Dashboard (v2.3)

### Features
- **Real-time Monitoring**: Live command/response tracking
- **System Metrics**: Performance statistics and uptime
- **Remote Control**: Start/stop instruments, send commands
- **Configuration Upload**: Upload new instrument definitions
- **Interactive Console**: Monitor all SCPI communications

### Access
- **URL**: http://localhost:8081
- **Requirements**: `pip install flask flask-socketio`

### Dashboard Sections
1. **System Overview**: Metrics, controls, and configuration upload
2. **Live Console**: Real-time command monitoring with WebSocket updates
3. **Instrument Grid**: Individual instrument status and controls

## 🏗️ Architecture

### Key Components

- **`SCPIInstrument`**: Virtual instrument with command handling and state management
- **`SCPIServer`**: TCP server for individual instruments with VISA compatibility
- **`SCPIEmulatorManager`**: Manages multiple instruments and servers
- **`WebDashboard`**: Flask-based real-time monitoring interface
- **`ExcelReader`**: CSV/Excel file parsing with automatic delimiter detection

### State Management

- **Stateful Commands**: SET/QUERY pairs automatically linked (e.g., `VOLT 5.0` + `VOLT?`)
- **VISA Device Clear**: Proper state reset on connection (like real instruments)
- **Error Queue**: SCPI-compliant error handling with `-xxx,"Error message"` format
- **Validation Preservation**: Input validation survives device clear operations

### Communication Flow

```
LabVIEW/VISA Client → TCP Socket → SCPI Server → SCPI Instrument → Command Processing
                                                      ↓
Web Dashboard ← WebSocket ← Command Logger ← Response/State Updates
```

## 🧪 Testing & Development

### Interactive Mode

```bash
scpi-emulator --interactive

# Commands:
# load <file>     - Load instrument definitions
# list            - Show loaded instruments  
# start           - Start all servers
# web             - Start web dashboard
# stop            - Stop all servers
# status          - Show server status
```

### Validation Testing

The debug instrument (port 5559) provides comprehensive validation testing:

```python
# Test all validation types
TEST_RANGE 7        # Range validation (1-10)
TEST_ENUM B         # Enum validation (A,B,C)  
TEST_BOOL ON        # Boolean validation (ON/OFF/1/0)
TEST_NOVALIDATION X # No validation (accepts anything)
```

### Unit Testing

```bash
# Run test suite (if implemented)
python -m pytest tests/

# Test specific instrument
python test_instrument.py --instrument debug_test_instrument
```

## 🐛 Troubleshooting

### Common Issues

**Connection Refused**
```bash
# Check if port is available
netstat -an | grep 5555
# Or try different port
scpi-emulator --load config.csv --port 6000 --start
```

**LabVIEW VISA Errors**
```bash
# Enable verbose logging to see VISA device clear simulation
scpi-emulator --load config.csv --start --verbose
# Look for [VISA-CLR] logs
```

**Validation Not Working**
```bash
# Check validation column in CSV
# Test with debug instrument first
telnet localhost 5559
TEST_RANGE 15    # Should fail for range:1,10
SYST:ERR?        # Check error queue
```

**Web Dashboard Not Loading**
```bash
# Install Flask dependencies
pip install flask flask-socketio
>>>requirement python 3.13 or later

# Check dashboard port
scpi-emulator --web --web-port 8082
```

### Debug Logging

Enable verbose logging for detailed troubleshooting:

```bash
scpi-emulator --load config.csv --start --verbose
```

Look for these log patterns:
- `[VISA-CLR]`: VISA device clear operations
- `[CMD]`: Incoming commands
- `[RSP]`: Outgoing responses
- `[VALIDATION]`: Input validation results
- `[LINK]`: Stateful command linking

## 📚 Examples

### Example 1: Basic DMM Emulation

```csv
Equipment,Port,Command,Response,Validation
Keysight 34461A,5555,*IDN?,Keysight Technologies,34461A,MY12345678,A.02.14-02.40-02.14-00.49-02-01,
,,MEAS:VOLT:DC?,1.234567E+00,
,,CONF:VOLT:DC (.+),OK,range:0.1,1000
,,CONF:VOLT:DC?,VOLT +1.000000E+01,+3.000000E-06,
```

### Example 2: Power Supply with Validation

```csv
Equipment,Port,Command,Response,Validation
Keysight E36312A,5556,VOLT (.+),OK,range:0,30
,,VOLT?,5.000000E+00,
,,CURR (.+),OK,range:0,3
,,CURR?,1.000000E+00,
,,OUTP (.+),OK,bool
,,OUTP?,1,
```

### Example 3: LabVIEW Integration

```labview
// LabVIEW code example
VISA Resource Name: TCPIP0::localhost::5555::SOCKET
VISA Write: "*IDN?"
VISA Read: "Keysight Technologies,34461A,MY12345678,A.02.14"
```

## 🔄 Version History

### v2.3 (Latest) - Web Dashboard Release
- ✅ **NEW**: Web dashboard with real-time monitoring
- ✅ **NEW**: REST API for remote control
- ✅ **NEW**: WebSocket integration for live updates
- ✅ **NEW**: Configuration upload via web interface
- ✅ Enhanced Flask-based architecture

### v2.2 - Validation Preservation Fix
- ✅ **FIXED**: Validation preservation during VISA device clear operations
- ✅ Enhanced validation debugging and error reporting
- ✅ Improved stateful command behavior across reconnections

### v2.1 - Enhanced Validation
- ✅ Enhanced range validation extraction and processing
- ✅ Fixed validation tracking in stateful commands
- ✅ Improved logging for debugging validation issues

### v2.0 - VISA Compatibility
- ✅ Removed welcome message for full VISA/NI-MAX compatibility
- ✅ Added comprehensive IEEE 488.2 command support
- ✅ Lambda closure fixes for command handlers

## 🤝 Contributing

### Development Setup

```bash
# Clone and setup development environment
git clone https://github.com/dannylyzethis/py-scpi-server.git
cd py-scpi-server

# Install development dependencies
python -m pip install -e ".[all,dev]"

# Run tests
python -m pytest tests/

# Run linting
ruff check src tests
```

### Contribution Guidelines

1. **Fork** the repository
2. **Create** a feature branch (`git checkout -b feature/amazing-feature`)
3. **Commit** your changes (`git commit -m 'Add amazing feature'`)
4. **Push** to the branch (`git push origin feature/amazing-feature`)
5. **Open** a Pull Request

### Code Style

- Follow PEP 8 guidelines
- Use meaningful variable names
- Add docstrings for new functions
- Include unit tests for new features
- Update documentation for API changes

## 📄 License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.

## 🆘 Support

- **Issues**: [GitHub Issues](https://github.com/dannylyzethis/py-scpi-server/issues)
- **Discussions**: [GitHub Discussions](https://github.com/dannylyzethis/py-scpi-server/discussions)
- **Documentation**: See `/docs` folder for detailed guides
- **Email**: your.email@example.com

## 🙏 Acknowledgments

- **SCPI Specification**: [SCPI-99](https://www.ivifoundation.org/docs/scpi-99.pdf)
- **IEEE 488.2 Standard**: For programmable instruments
- **LabVIEW Community**: For testing and feedback
- **Python Community**: For excellent networking libraries

## 🌟 Star History

[![Star History Chart](https://api.star-history.com/svg?repos=dannylyzethis/py-scpi-server&type=Date)](https://star-history.com/#dannylyzethis/py-scpi-server&Date)

## 📊 Project Stats

![GitHub repo size](https://img.shields.io/github/repo-size/dannylyzethis/py-scpi-server)
![GitHub code size in bytes](https://img.shields.io/github/languages/code-size/dannylyzethis/py-scpi-server)
![GitHub last commit](https://img.shields.io/github/last-commit/dannylyzethis/py-scpi-server)
![GitHub issues](https://img.shields.io/github/issues/dannylyzethis/py-scpi-server)
![GitHub pull requests](https://img.shields.io/github/issues-pr/dannylyzethis/py-scpi-server)

---

**Made with ❤️ for the test automation community**

*Star ⭐ this repository if you found it helpful!*
