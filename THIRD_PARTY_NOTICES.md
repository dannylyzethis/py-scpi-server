# Third-party dependency notices

This project is MIT licensed. Its base installation has no mandatory runtime dependencies.
Optional Excel, web-dashboard, discovery, and development features install the packages listed in
`licenses/dependencies.json`; those packages are not copied into this project's wheel.

The machine-readable inventory records the reviewed version, dependency scope, and SPDX license for
every package in the `all` and `dev` dependency closure. Run `python tools/check_licenses.py` after
installing `.[all,dev]` to verify the installed closure against that review.

## Runtime and optional-feature dependencies

| Feature | Direct dependency | License | Notable transitive dependencies |
|---|---|---|---|
| Excel | openpyxl | MIT | et-xmlfile (MIT) |
| Dashboard | Flask | BSD-3-Clause | Blinker (MIT), Click/ItsDangerous/Jinja2/MarkupSafe/Werkzeug (BSD/MIT) |
| Dashboard | Flask-SocketIO | MIT | python-socketio and python-engineio (MIT), bidict (MPL-2.0), simple-websocket/wsproto/h11 (MIT) |
| Discovery | zeroconf | LGPL-2.1-or-later | ifaddr (MIT), typing-extensions (PSF-2.0) |

### bidict — MPL-2.0

`bidict` is an unmodified transitive dependency of `python-socketio`. The Mozilla Public License
2.0 applies to the `bidict` files, not to this project's independently written MIT-licensed files.
Recipients can obtain its source and license from <https://github.com/jab/bidict>.

### zeroconf — LGPL-2.1-or-later

`zeroconf` is an optional, dynamically imported discovery dependency and is not bundled into this
project's wheel or container source tree. The LGPL applies to the installed `zeroconf` library.
Recipients can obtain its source and license from <https://github.com/python-zeroconf/python-zeroconf>.

### Socket.IO Python packages — MIT

Flask-SocketIO, python-socketio, python-engineio, and simple-websocket are separate MIT packages
installed by the `web` extra. Their source is available from <https://github.com/miguelgrinberg>.
No JavaScript Socket.IO distribution is vendored in this repository.

## Development-only dependencies

Build, pytest, pytest-cov, PyVISA, PyVISA-py, and Ruff are development-only direct dependencies.
Their licenses and transitive package licenses are recorded in the machine-readable inventory.
Development tools are not included in the project wheel or runtime container.

This notice is an engineering compliance record, not legal advice. When dependency versions change,
review upstream license files and update the inventory and this notice before accepting the change.
