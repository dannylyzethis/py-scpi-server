# Fast-CW and arbitrary-waveform stimulus

The virtual VNA provides two project-owned stimulus applications for early automation and DUT
scenario development.

Fast-CW mode is configured with `SENS:FCW:STAT`, `SENS:FCW:FREQ`, and `SENS:FCW:DWEL`. When
enabled, the selected CW frequency is returned for every point from `CALC:X?`; trace length and
the normal trigger path remain unchanged.

Arbitrary-waveform mode is enabled with `SENS:AWG:STAT`. An attached scenario stream named
`arbitrary_waveform.envelope` supplies one complex envelope value per trace point. Each normal
VNA sample is multiplied by `offset + scale * envelope`, where the controls are
`SENS:AWG:OFFS` and `SENS:AWG:SCAL`. Without that stream, an envelope of one is used so scripts
can configure the application before a scenario is attached.

Both families are capability-gated, survive `*CLS`, reset with `*RST`, and compose with the
other VNA trace applications.
