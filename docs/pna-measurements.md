# VNA measurement workflows

The VNA emulator now models the configuration relationships that ordinary automation code expects,
rather than treating CALCulate and DISPlay commands as unrelated canned responses.

## State model

Each instrument owns an isolated measurement system:

- channels contain ordered, uniquely named measurements;
- each measurement has a measurement number, parameter type, display format, math configuration,
  math-memory trace, markers, limit state, and equation state;
- display windows contain numbered traces, and each trace can feed one existing measurement;
- the selected measurement, active channel, active window, and active trace move together when a
  measurement or display trace is selected.

The supported bounds match the modeled VNA workflow: channels 1–200, windows 1–24, traces 1–24 per
window, and markers 1–15 per measurement. Invalid or nonexistent addresses enter the normal SCPI
error queue instead of leaking Python failures or silently creating impossible state.

## Preset and clearing behavior

A new instrument and `*RST` start from a coherent preset: channel 1 contains selected measurement
`CH1_S11_1`, and window 1 trace 1 displays it. This gives driver code a usable selected context
immediately.

`*CLS` clears event and error reporting only. Device Clear resets transport/output synchronization
only. Neither operation erases channels, measurements, traces, formats, or marker configuration.
That separation is essential: on a real instrument, clearing status must not make the command
processor unusable or destroy the measurement setup.

## Implemented command families

The typed registry accepts full and legal abbreviated headers, including indexed CALCulate and
DISPlay forms. The current lifecycle covers:

- extended and legacy measurement definition, catalog, selection, modification, and deletion;
- measurement-number, window-number, and trace-number context queries;
- display channel/window state, trace feed, selection, visibility, title, and catalogs;
- display format and math function, memory, and interpolation state;
- marker enable, X position, bucket position, format, Y result, searches, and all-markers-off;
- limit state/result and equation text/state;
- active channel and active measurement queries.

Marker Y values are deterministic today so configuration and parsing code can be developed now.
The subsequent VNA data issue will connect them, math memory, and trace reads to the shared scenario
engine so queued complex DUT traces drive coherent results across commands.

## Example

```text
CALC2:PAR:DEF:EXT 'Gain','S21'
DISP:WIND2:STAT ON
DISP:WIND2:TRAC1:FEED 'Gain'
DISP:WIND2:TRAC1:SEL
CALC2:FORM MLOG
CALC2:MARK1:STAT ON
CALC2:MARK1:X 2.45GHZ
SYST:ACT:CHAN?
CALC2:PAR:CAT:EXT?
```

This sequence creates real linked state: deleting `Gain` also removes its display feed, selecting
the trace activates channel 2 and `Gain`, and later queries observe the same configuration.

## Scope boundary

This layer owns existence and configuration relationships. It does not yet model sweep timing,
receiver physics, calibration math, or application-specific results. The adjacent sweep layer now
supplies linear, logarithmic, CW, power, and segment X-axes and deterministic acquisition timing;
the generic scenario engine will supply the DUT values without rebuilding either state layer.
