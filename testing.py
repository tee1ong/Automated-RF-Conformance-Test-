# -*- coding: utf-8 -*-
"""
Created on Fri Oct 23 17:01:32 2026

@author: tll32
"""
"""
test_instrument.py

Minimal demonstration/test of the Instrument base class.
Uses a FAKE comms link so the class can be exercised without hardware.
.
"""

import instrument as inst_mod
from instrument import (Instrument, SignalGenerator, SpectrumAnalyser,
    run_measurement, evaluate_compliance, TestConfig, Result,
)

# ---------------------------------------------------------------------------
# Test double for the assumed comms link.
# ---------------------------------------------------------------------------

class _FakeLink:
    """Stand-in for the object returned by the assumed open_link()."""

    def __init__(self, address):
        self.address = address
        self.open = True
        self.sent = []          # record commands, so tests can assert on them
        self.queried = []

    def send(self, cmd):
        if not self.open:
            raise RuntimeError("send on closed fake link")
        self.sent.append(cmd)
        print(f"[fake] send  {self.address!r} <- {cmd!r}")

    def query(self, cmd):
        if not self.open:
            raise RuntimeError("query on closed fake link")
        self.queried.append(cmd)
        print(f"[fake] query {self.address!r} <- {cmd!r}")
        if cmd == ":MEAS:POW?":
            return "-10.3"
        if cmd == "SYST:ERR?":
            return '+0,"No error"'
        if cmd == "*OPC?":
            return "1"
        return "FAKE-IDN"
    
    def close(self):
        print(f"[fake] close {self.address!r}")
        self.open = False


def _fake_open_link(address, timeout_s=5.0):
    print(f"[fake] open_link({address!r}, timeout_s={timeout_s})")
    return _FakeLink(address)


def _install_fake_comms():
    """Replace the assumed comms layer with the fake for the duration of a test."""
    inst_mod.open_link = _fake_open_link


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_happy_path():
    """Open, send, query, close — the normal flow."""
    _install_fake_comms()
    with Instrument("FAKE::generator", "generator") as inst:
        inst.send(":SOUR:FREQ 2.4e9")
        idn = inst.query("*IDN?")
        assert idn == "FAKE-IDN"
        assert inst._link is not None            # still connected inside `with`
    assert inst._link is None                    # closed by __exit__
    print("[test] happy path: OK")


def test_cleanup_runs_on_exception():
    """Close must run even when the with-body raises."""
    _install_fake_comms()
    inst = Instrument("FAKE::analyser", "analyser")
    try:
        with inst:
            raise RuntimeError("simulated failure")
    except RuntimeError:
        pass
    assert inst._link is None                    # __exit__ closed it
    print("[test] cleanup on exception: OK")


def test_send_before_connect_fails_clearly():
    """send() on a disconnected instrument raises a clear error."""
    _install_fake_comms()
    inst = Instrument("FAKE::generator", "generator")
    try:
        inst.send(":SOUR:FREQ 2.4e9")
        assert False, "expected RuntimeError"
    except RuntimeError as e:
        assert "not connected" in str(e)
    print("[test] send before connect: OK")


def test_query_before_connect_fails_clearly():
    """query() on a disconnected instrument raises a clear error."""
    _install_fake_comms()
    inst = Instrument("FAKE::generator", "generator")
    try:
        inst.query("*IDN?")
        assert False, "expected RuntimeError"
    except RuntimeError as e:
        assert "not connected" in str(e)
    print("[test] query before connect: OK")


def test_double_connect_rejected():
    """Calling connect() twice must raise, not leak a second session."""
    _install_fake_comms()
    inst = Instrument("FAKE::generator", "generator")
    inst.connect()
    try:
        inst.connect()
        assert False, "expected RuntimeError"
    except RuntimeError as e:
        assert "already connected" in str(e)
    inst.close()
    print("[test] double connect rejected: OK")


def test_close_is_idempotent():
    """close() before connect, then twice after connect, must all be safe."""
    _install_fake_comms()
    inst = Instrument("FAKE::generator", "generator")
    inst.close()          # never connected — must be a no-op
    inst.connect()
    inst.close()
    inst.close()          # already closed — must be a no-op
    print("[test] close is idempotent: OK")


def test_commands_reach_the_link_in_order():
    """What the Instrument sends is exactly what reaches the assumed link."""
    _install_fake_comms()
    with Instrument("FAKE::generator", "generator") as inst:
        inst.send(":SOUR:FREQ 2.4e9")
        inst.send(":SOUR:POW -10")
        assert inst._link.sent == [":SOUR:FREQ 2.4e9", ":SOUR:POW -10"]
    print("[test] commands reach link in order: OK")

def test_generator_commands_reach_generator_link_only():
    """Generator config sends exactly the expected SCPI, to its own link."""
    _install_fake_comms()
    with SignalGenerator("FAKE::gen", "generator") as gen:
        gen.configure(2.4e9, -10.0)
        gen.output_on()
        assert gen._link.sent == [
            "*RST",
            ":SOUR:FREQ 2400000000.0",
            ":SOUR:POW -10.0",
            ":OUTP ON",
        ]
    print("[test] generator commands reach generator link only: OK")


def test_analyser_commands_reach_analyser_link_only():
    """Analyser config sends exactly the expected SCPI, to its own link."""
    _install_fake_comms()
    with SpectrumAnalyser("FAKE::sa", "analyser") as sa:
        sa.configure(2.4e9, 10e6, 100e3)
        assert sa._link.sent == [
            "*RST",
            ":SENS:FREQ:CENT 2400000000.0",
            ":SENS:FREQ:SPAN 10000000.0",
            ":SENS:BAND:RES 100000.0",
        ]
    print("[test] analyser commands reach analyser link only: OK")
    
def test_measurement_flow():
    """Full flow: configure → output on → synchronise → measure; returns a number."""
    _install_fake_comms()
    with SignalGenerator("FAKE::gen", "generator") as gen, \
         SpectrumAnalyser("FAKE::sa", "analyser") as sa:
        measured = run_measurement(gen, sa, TestConfig())
        assert measured == -10.3
        # spot-check commands reached the right instruments
        assert gen._link.sent == [
            "*RST",
            ":SOUR:FREQ 2400000000.0",
            ":SOUR:POW -10.0",
            ":OUTP ON",
        ]
        assert sa._link.sent == [
            "*RST",
            ":SENS:FREQ:CENT 2400000000.0",
            ":SENS:FREQ:SPAN 10000000.0",
            ":SENS:BAND:RES 100000.0",
        ]
        # queried is now interleaved with SYST:ERR? checks — assert membership
        assert ":MEAS:POW?" in sa._link.queried
    print(f"[test] measurement flow: OK (measured={measured} dBm)")
    
def test_compliance_pass():
    """A measured value comfortably inside the window -> PASS."""
    r = evaluate_compliance(-10.3, -11.0, -9.0, 2.4e9, -10.0)
    assert r.verdict == "PASS"
    assert r.margin_db == 0.7
    print("[test] compliance PASS: OK")


def test_compliance_fail_below():
    """A measured value below the lower limit -> FAIL, negative margin."""
    r = evaluate_compliance(-11.5, -11.0, -9.0, 2.4e9, -10.0)
    assert r.verdict == "FAIL"
    assert r.margin_db == -0.5
    print("[test] compliance FAIL (below): OK")


def test_compliance_fail_above():
    """A measured value above the upper limit -> FAIL, negative margin."""
    r = evaluate_compliance(-8.5, -11.0, -9.0, 2.4e9, -10.0)
    assert r.verdict == "FAIL"
    assert r.margin_db == -0.5
    print("[test] compliance FAIL (above): OK")


def test_compliance_on_limit_passes():
    """Exactly on a limit counts as PASS (margin 0)."""
    r = evaluate_compliance(-11.0, -11.0, -9.0, 2.4e9, -10.0)
    assert r.verdict == "PASS"
    assert r.margin_db == 0.0
    print("[test] compliance on limit: OK")


def test_report_json_writes_valid_file():
    """JSON reporter writes a valid, parseable file."""
    import json, os, tempfile
    r = evaluate_compliance(-10.3, -11.0, -9.0, 2.4e9, -10.0)
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "result.json")
        from instrument import report_json
        report_json(r, path)
        with open(path) as f:
            data = json.load(f)
        assert data["verdict"] == "PASS"
        assert data["margin_db"] == 0.7
        assert data["measured_dbm"] == -10.3
    print("[test] JSON report: OK")
    
def test_generator_rf_off_on_exit():
    """RF output is disabled when the generator's with-block exits."""
    _install_fake_comms()
    gen = SignalGenerator("FAKE::gen", "generator")
    with gen:
        gen.output_on()
        link = gen._link            # keep a reference before close resets it
    assert link.sent[-1] == ":OUTP OFF"
    print("[test] generator RF off on exit: OK")

def test_measurement_implausible_raises():
    """A physically implausible measurement raises rather than producing a verdict."""
    _install_fake_comms()

    class _ImplausibleLink(_FakeLink):
        def query(self, cmd):
            if cmd == ":MEAS:POW?":
                return "999.0"
            return super().query(cmd)

    inst_mod.open_link = lambda address, timeout_s=5.0: _ImplausibleLink(address)

    with SpectrumAnalyser("FAKE::sa", "analyser") as sa:
        try:
            sa.measure_power()
            assert False, "expected ValueError"
        except ValueError as e:
            assert "implausible" in str(e)
    print("[test] implausible measurement raises: OK")

def test_scpi_error_queue_checked():
    """A non-zero SYST:ERR? code raises an error."""
    _install_fake_comms()

    class _ErrorLink(_FakeLink):
        def query(self, cmd):
            if cmd == "SYST:ERR?":
                return '-222,"Data out of range"'
            return super().query(cmd)

    inst_mod.open_link = lambda address, timeout_s=5.0: _ErrorLink(address)

    with Instrument("FAKE::gen", "generator") as inst:
        try:
            inst.check_errors()
            assert False, "expected RuntimeError"
        except RuntimeError as e:
            assert "SCPI error" in str(e)
    print("[test] SCPI error queue checked: OK")
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    tests = [
        test_happy_path,
        test_cleanup_runs_on_exception,
        test_send_before_connect_fails_clearly,
        test_query_before_connect_fails_clearly,
        test_double_connect_rejected,
        test_close_is_idempotent,
        test_commands_reach_the_link_in_order,
        test_generator_commands_reach_generator_link_only,      
        test_analyser_commands_reach_analyser_link_only,        
        test_measurement_flow,
        test_compliance_pass,
        test_compliance_fail_below,
        test_compliance_fail_above,
        test_compliance_on_limit_passes,
        test_report_json_writes_valid_file,
        test_generator_rf_off_on_exit,
        test_measurement_implausible_raises,
        test_scpi_error_queue_checked,
    ]
    for t in tests:
        t()
    print(f"\n[test] all {len(tests)} checks passed")
    

