# Automated-RF-Conformance-Test-
This report presents the proposal of an automated RF conformance test in Python. A signal generator and
spectrum analyser are configured over assumed SCPI/LAN comms. Power measurement is taken, compared against a two-sided spec window, and reported as human-readable and JSON output. The
workflow is exercised by a unit-test suite against a fake link.

The objective is to design a Python solution that configures the instruments, executes a measurement, compares the result against a specification limit, calculates the compliance margin, reports in both human
and machine-readable form, and demonstrates error handling and instrument cleanup. Low-level
instrument communication functions already exist and the measured value is available from the analyser
API the solution therefore does not attend to transport. The system consists of a control computer as a remote SCPI client to two instruments: a signal generator
that produces a controlled RF signal, and a spectrum analyser that measures it. The generator's output is
cabled to the analyser's input; the control computer is not in the RF signal path, it only sends configuration
commands and queries over LAN and reads back measured values.
