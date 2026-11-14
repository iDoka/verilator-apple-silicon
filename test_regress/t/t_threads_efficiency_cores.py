#!/usr/bin/env python3
# DESCRIPTION: Verilator: Verilog Test driver/expect definition
#
# This program is free software; you can redistribute it and/or modify it
# under the terms of either the GNU Lesser General Public License Version 3
# or the Perl Artistic License Version 2.0.
# SPDX-FileCopyrightText: 2026 Wilson Snyder
# SPDX-License-Identifier: LGPL-3.0-only OR Artistic-2.0

import platform
import subprocess

import vltest_bootstrap

test.scenarios('vltmt')
test.top_filename = "t/t_threads_counter.v"


def sysctl(name):
    return int(subprocess.check_output(['sysctl', '-n', name], text=True))


if platform.system() != 'Darwin' or sysctl('hw.nperflevels') < 2:
    test.skip("No performance and efficiency cores")

perf_threads = sysctl('hw.perflevel0.logicalcpu')

# One thread more than the performance cores, so one thread lands on an efficiency core
test.compile(verilator_flags2=['--cc'], threads=perf_threads + 1)

test.execute()

test.file_grep(
    test.run_log_filename, r'%Warning: Process has ' + str(perf_threads) +
    r' performance core hardware threads, but model was Verilated with --threads ' +
    str(perf_threads + 1) +
    r'\. Threads on efficiency cores will likely cause significant slowdown\.')

test.passes()
