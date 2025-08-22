# SCPI Equipment Emulator

[![Python Version](https://img.shields.io/badge/python-3.6+-blue.svg)](https://python.org)
[![License](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)

A comprehensive SCPI (Standard Commands for Programmable Instruments) equipment emulator that provides virtual instrument functionality over TCP/IP. Perfect for testing LabVIEW applications, VISA drivers, and instrument automation without physical hardware.

## ✨ Features

- **LabVIEW Compatible**: Full VISA TCP/IP support (`TCPIP0::localhost::<port>::INSTR`)
- **Multiple Instruments**: Run multiple virtual instruments simultaneously on different ports
- **Excel/CSV Configuration**: Define instruments and commands using simple spreadsheets
- **IEEE 488.2 Compliance**: Built-in standard SCPI commands (`*IDN?`, `*RST`, etc.)
- **Stateful Commands**: SET/QUERY command pairs with persistent state
- **Input Validation**: Range, enum, and boolean validation with proper SCPI error handling
- **VISA Device Clear**: Proper simulation of VISA device clear operations
- **Zero Dependencies**: Pure Python 3.6+ with no external dependencies for CSV files

## 🚀 Quick Start

### Installation

```bash
# Clone the repository
git clone https://github.com/yourusername/scpi-emulator.git
cd scpi-emulator

# No installation required! Pure Python.
# For Excel support (optional):
pip install openpyxl
```

### Create Example Configuration

```bash
python scpi_emulator.py --create-example
```

### Start Emulator

```bash
# Load configuration and start servers
python scpi_emulator.py --load scpi_instruments_example.csv --start

# With verbose logging
python scpi_emulator.py --load scpi_instruments_example.csv --start --verbose
```

### Connect from LabVIEW

Use these VISA resource strings in LabVIEW:
- **Keysight 34461A DMM**: `TCPIP0::localhost::5555::INSTR`
- **Keysight E36312A PSU**: `TCPIP0::localhost::5556::INSTR`
- **Tektronix TDS2024B Scope**: `TCPIP0::localhost::5557::INSTR`
- **Agilent 33220A Generator**: `TCPIP0::localhost::5558::INSTR`
- **Debug Test Instrument**: `TCPIP0::localhost::5559::INSTR`

## 📋 Configuration Format

Define your instruments in CSV or Excel format:

| Equipment | Port | Command | Response | Validation |
|-----------|------|---------|----------|------------|
| My DMM | 5555 | `*IDN?` | `MY_DMM,MODEL123,SERIAL456,1.0` | |
| | | `MEAS:VOLT:DC?` | `1.234567E+00` | |
| | | `VOLT (.+)` | `OK` | `range:0,10` |
| | | `VOLT?` | `5.0` | |

### Validation Rules

- **Range**: `range:min,max` (e.g., `range:0,10` for 0-10V)
- **Enum**: `enum:VAL1,VAL2,VAL3` (e.g., `enum:AC,DC,GND`)
- **Boolean**: `bool` (accepts `ON/OFF/1/0`)

## 🔧 Command Line Options

```bash
python scpi_emulator.py [OPTIONS]

Options:
  --load, -l FILE          Load instrument definitions (.csv, .xlsx, .xls)
  --start, -s              Start TCP servers immediately
  --port, -p PORT          Starting port number (default: 5555)
  --host HOST              Server host (default: localhost)
  --create-example         Create example CSV file
  --interactive, -i        Start interactive mode
  --verbose, -v            Enable verbose logging
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
TEST_RANGE 5            # ✅ Valid: returns "OK" 
TEST_RANGE 15           # ❌ Invalid: adds error to queue
SYST:ERR?               # Check error: "-222,Data out of range..."

# Test enum validation
TEST_ENUM B             # ✅ Valid: accepts A, B, or C
TEST_ENUM X             # ❌ Invalid: not in enum list

# Test boolean validation  
TEST_BOOL ON            # ✅ Valid: accepts ON/OFF/1/0
TEST_BOOL MAYBE         # ❌ Invalid: not a boolean
```

## 🏗️ Architecture

### Key Components

- **`SCPIInstrument`**: Virtual instrument with command handling and state management
- **`SCPIServer`**: TCP server for individual instruments with VISA compatibility
- **`SCPIEmulatorManager`**: Manages multiple instruments and servers
- **`ExcelReader`**: CSV/Excel file parsing with automatic delimiter detection

### State Management

- **Stateful Commands**: SET/QUERY pairs automatically linked (e.g., `VOLT 5.0` + `VOLT?`)
- **VISA Device Clear**: Proper state reset on connection (like real instruments)
- **Error Queue**: SCPI-compliant error handling with `-xxx,"Error message"` format

## 🧪 Testing & Development

### Interactive Mode

```bash
python scpi_emulator.py --interactive

# Commands:
# load <file>     - Load instrument definitions
# list            - Show loaded instruments  
# start           - Start all servers
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

## 🐛 Troubleshooting

### Common Issues

**Connection Refused**
```bash
# Check if port is available
netstat -an | grep 5555
# Or try different port
python scpi_emulator.py --load config.csv --port 6000 --start
```

**LabVIEW VISA Errors**
```bash
# Enable verbose logging to see VISA device clear simulation
python scpi_emulator.py --load config.csv --start --verbose
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

## 🔄 Version History

### v2.2 (Latest)
- ✅ **FIXED**: Validation preservation during VISA device clear operations
- ✅ Enhanced validation debugging and error reporting
- ✅ Improved stateful command behavior across reconnections

### v2.1
- ✅ Enhanced range validation extraction and processing
- ✅ Fixed validation tracking in stateful commands
- ✅ Improved logging for debugging validation issues

### v2.0
- ✅ Removed welcome message for full VISA/NI-MAX compatibility
- ✅ Added comprehensive IEEE 488.2 command support
- ✅ Lambda closure fixes for command handlers

## 📄 License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.

## 🤝 Contributing

1. Fork the repository
2. Create a feature branch (`git checkout -b feature/amazing-feature`)
3. Commit your changes (`git commit -m 'Add amazing feature'`)
4. Push to the branch (`git push origin feature/amazing-feature`)
5. Open a Pull Request

## 🆘 Support

- **Issues**: [GitHub Issues](https://github.com/yourusername/scpi-emulator/issues)
- **Discussions**: [GitHub Discussions](https://github.com/yourusername/scpi-emulator/discussions)
- **Documentation**: See `/docs` folder for detailed guides

## 🙏 Acknowledgments

- SCPI specification: [SCPI-99](https://www.ivifoundation.org/docs/scpi-99.pdf)
- IEEE 488.2 standard for programmable instruments
- Python community for excellent networking libraries

---

**Made with ❤️ for the test automation community**