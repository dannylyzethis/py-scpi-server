# Generic N-port measurements

The N-port application uses the configured virtual instrument shape; it contains no fixed
commercial model assumptions. Enable it with `SENS:NPORT:STAT ON`.

Create and select a measurement with `CALC:NPORT:DEF "name",receiver,source`. For example,
`CALC:NPORT:DEF "Forward41",4,1` creates an `S41` measurement on a four-port VNA. Both port
numbers must exist on the selected two-port or four-port profile. `CALC:NPORT:PAR? receiver,source`
returns the corresponding generic parameter name, and `CALC:NPORT:CAT?` lists every valid
receiver/source combination for the configured port count. Remove a named measurement with
`CALC:NPORT:DEL "name"`.

`CALC:NPORT:DATA? receiver,source` returns complex real/imaginary pairs. When an attached
scenario provides a matching stream such as `S41`, that stream supplies the result. Otherwise
the selected matching measurement data is used, or a deterministic zero trace is returned.

Invalid ports produce a SCPI data-range error. The application is capability-gated, survives
`*CLS`, resets with `*RST`, and uses the same scenario advancement rules as other VNA data.
