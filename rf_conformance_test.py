# -*- coding: utf-8 -*-
"""
Created on Thu Oct 22 21:55:38 2026

@author: tll32
"""
"""
rf_conformance_test.py

Automated RF conformance test for a signal generator + spectrum analyser pair.

The script runs on a control computer as a remote SCPI client over LAN. The
low-level comms layer (connect/send/query/close) is assumed to already exist
Workflow: configure both instruments, enable RF, synchronise, measure,
compare against a two-sided spec window, report PASS/FAIL with margin in
human-readable and JSON forms. See the accompanying design note for the
rationale behind each design choice.
"""
import json
import os
import sys
from dataclasses import dataclass, asdict
from datetime import datetime, timezone

@dataclass(frozen=True)
class TestConfig:
    test_name: str = "rf_power_conformance"
    frequency_hz: float = 2.4e9
    setpoint_dbm: float = -10.0
    span_hz: float = 10e6
    rbw_hz: float = 100e3
    lower_limit_dbm: float = -11.0
    upper_limit_dbm: float = -9.0


def open_link(address: str, timeout_s: float = 5.0):
    """Open a SCPI session to `address` and return a link object.

    Assumed to be provided by the existing instrument comms layer.
    - `address`: VISA-style resource string identifying one instrument.
    - `timeout_s`: connection timeout, delegated to the underlying layer.

    The returned link is expected to provide .send(cmd), .query(cmd) -> str,
    and .close().
    """
    raise NotImplementedError("Provided by the existing instrument comms layer")


# ---------------------------------------------------------------------------
# Instrument: lifecycle wrapper around one assumed comms link.
# ---------------------------------------------------------------------------

class Instrument:
    def __init__(self, address: str, name: str) -> None:
        self.address = address
        self.name = name
        self._link = None

    # -- lifecycle ---------------------------------------------------------

    def connect(self) -> None:
        """Open the SCPI session.

        Raises if already connected. If open_link() fails, its exception
        propagates and self._link stays None, leaving the object disconnected.
        """
        if self._link is not None:
            raise RuntimeError(f"{self.name}: already connected")
        self._link = open_link(self.address)   # module-level assumed factory

    def close(self) -> None:
        """Close the SCPI session if open. Safe to call more than once."""
        if self._link is not None:
            self._link.close()
            self._link = None

    def __enter__(self) -> "Instrument":
        self.connect()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        # Runs whether the body succeeded, failed, or raised.
        self.close()

    # -- delegation to the assumed link -----------------------------------

    def send(self, cmd: str) -> None:
        """Send a write-only SCPI command."""
        if self._link is None:
            raise RuntimeError(f"{self.name}: not connected")
        self._link.send(cmd)

    def query(self, cmd: str) -> str:
        """Send a SCPI query and return the raw response string."""
        if self._link is None:
            raise RuntimeError(f"{self.name}: not connected")
        return self._link.query(cmd)
    
    def check_errors(self) -> None:
        """Raise if the instrument's error queue is non-empty."""
        response = self.query("SYST:ERR?").strip()
        code = response.split(",", 1)[0].strip()
        if code not in ("0", "+0"):
            raise RuntimeError(f"{self.name}: SCPI error: {response}")
    
# ---------------------------------------------------------------------------
# Instrument-specific subclasses.
#
# Each adds ONLY its own domain methods — the SCPI placeholder strings.
# Connection, send/query, and cleanup are inherited from Instrument.
# SCPI command text is a realistic placeholder (exact mnemonics vary by model).
# ---------------------------------------------------------------------------

class SignalGenerator(Instrument):
    """Stimulus source: produces a controlled RF signal."""

    def configure(self, freq_hz: float, level_dbm: float) -> None:
        """Reset to a known state and apply the stimulus settings."""
        self.send("*RST")                      # known starting state
        self.send(f":SOUR:FREQ {freq_hz}")     # placeholder
        self.send(f":SOUR:POW {level_dbm}")    # placeholder
        self.check_errors()
        # RF output is enabled separately so cleanup can pair it.

    def output_on(self) -> None:
        """Enable the RF output."""
        self.send(":OUTP ON")                  # placeholder
        self.check_errors()
        
    def output_off(self) -> None:
        """Disable the RF output."""
        self.send(":OUTP OFF")                 # placeholder

    def __exit__(self, exc_type, exc, tb):
       try:
           if self._link is not None:
               self.output_off()
       except Exception:
           print(f"[WARN] {self.name}: could not confirm RF output off")
       finally:
           super().__exit__(exc_type, exc, tb)


class SpectrumAnalyser(Instrument):
    """Measurement device: observes the RF signal."""

    def configure(self, centre_hz: float, span_hz: float, rbw_hz: float) -> None:
        """Reset to a known state and apply the measurement-window settings."""
        self.send("*RST")                          # known starting state
        self.send(f":SENS:FREQ:CENT {centre_hz}")  # placeholder
        self.send(f":SENS:FREQ:SPAN {span_hz}")    # placeholder
        self.send(f":SENS:BAND:RES {rbw_hz}")      # placeholder
    
    def measure_power(self) -> float:
        """Return the measured power in dBm."""
        value = float(self.query(":MEAS:POW?"))
        if not (-50.0 <= value <= 20.0):
            raise ValueError(f"implausible measurement: {value} dBm")
        return value
    
def run_measurement(gen: SignalGenerator, sa: SpectrumAnalyser, cfg: TestConfig) -> float:
    """Configure both instruments, enable RF, synchronise, and measure.

    Returns the measured power in dBm.
    Assumes both instruments are already connected (inside their `with` blocks).
    """
    gen.configure(cfg.frequency_hz, cfg.setpoint_dbm)
    sa.configure(cfg.frequency_hz, cfg.span_hz, cfg.rbw_hz)
    gen.output_on()
    sa.query("*OPC?")          # block until settings applied / sweep done
    return sa.measure_power()

# ---------------------------------------------------------------------------
# Compliance evaluation and reporting.
#
# Pure functions, deliberately outside the instrument classes: none of these
# is an instrument concern. evaluate_compliance() has no side effects, so it
# can be tested independently of any hardware.
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Result:
    """Outcome of one conformance measurement."""
    test_name: str
    timestamp: str
    frequency_hz: float
    setpoint_dbm: float
    measured_dbm: float
    lower_limit_dbm: float
    upper_limit_dbm: float
    margin_db: float
    verdict: str


def evaluate_compliance(
    measured_dbm: float,
    lower_dbm: float,
    upper_dbm: float,
    freq_hz: float,
    setpoint_dbm: float,
    test_name: str = "rf_power_conformance",
) -> Result:
    """Compare a measured value against a two-sided spec window.

    Margin convention: distance from the measured value to the NEAREST limit.
    Positive margin  -> inside the window (PASS), with that much headroom.
    Negative margin  -> outside the window (FAIL), by that amount.
    """
    margin = min(measured_dbm - lower_dbm, upper_dbm - measured_dbm)
    verdict = "PASS" if margin >= 0 else "FAIL"
    return Result(
        test_name=test_name,
        timestamp=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        frequency_hz=freq_hz,
        setpoint_dbm=setpoint_dbm,
        measured_dbm=measured_dbm,
        lower_limit_dbm=lower_dbm,
        upper_limit_dbm=upper_dbm,
        margin_db=round(margin, 3),
        verdict=verdict,
    )


def report_human(result: Result) -> None:
    """Print a human-readable summary of the result."""
    print()
    print(f"  Test      : {result.test_name}")
    print(f"  Frequency : {result.frequency_hz / 1e9:.3f} GHz")
    print(f"  Setpoint  : {result.setpoint_dbm:+.1f} dBm")
    print(f"  Measured  : {result.measured_dbm:+.2f} dBm")
    print(f"  Limits    : {result.lower_limit_dbm:+.1f} .. {result.upper_limit_dbm:+.1f} dBm")
    print(f"  Margin    : {result.margin_db:+.2f} dB")
    print(f"  Verdict   : {result.verdict}")
    print()


def report_json(result: Result, path: str) -> None:
    """Write the result as JSON for machine consumption."""
    with open(path, "w") as f:
        json.dump(asdict(result), f, indent=2)
    print(f"[report] machine-readable result written to {path}")
    
def main(gen_address: str, sa_address: str) -> int:
    """Run the conformance test end-to-end. Returns a process exit code."""
    cfg = TestConfig()          # defaults live in the dataclass
    try:
        with SignalGenerator(gen_address, "generator") as gen, \
             SpectrumAnalyser(sa_address, "analyser") as sa:
            measured = run_measurement(gen, sa, cfg)
            result = evaluate_compliance(
                measured, cfg.lower_limit_dbm, cfg.upper_limit_dbm,
                cfg.frequency_hz, cfg.setpoint_dbm, cfg.test_name,
            )
            report_human(result)
            report_json(result, "result.json")
            return 0 if result.verdict == "PASS" else 1
    except Exception as exc:
        print(f"[ERROR] {type(exc).__name__}: {exc}")
        return 2

if __name__ == "__main__":
    gen_address = os.environ.get("RF_GEN_ADDRESS", "")
    sa_address = os.environ.get("RF_SA_ADDRESS", "")

    if not gen_address or not sa_address:
        print("ERROR: set RF_GEN_ADDRESS and RF_SA_ADDRESS")
        sys.exit(2)

    sys.exit(main(gen_address, sa_address))