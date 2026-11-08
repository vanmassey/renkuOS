#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Test UDP datagram length validation from the production udp.cpp helper."""
from pathlib import Path
import os
import subprocess
import tempfile

root = (Path(os.environ['SOURCE_ROOT']) if 'SOURCE_ROOT' in os.environ
        else Path(__file__).resolve().parents[7])
source = (root / 'src/add-ons/kernel/network/protocols/udp/udp.cpp').read_text()
start = source.index('static inline bool\nudp_length_is_valid(')
function = source[start:source.index('\n}\n', start) + 3]

fixture = r'''
#include <assert.h>
#include <stddef.h>
#include <stdint.h>

typedef uint16_t uint16;

struct udp_header {
    uint16 source_port;
    uint16 destination_port;
    uint16 udp_length;
    uint16 udp_checksum;
};

'''

main = r'''
int
main()
{
    const size_t headerSize = sizeof(udp_header);

    assert(!udp_length_is_valid(0, 64));
    assert(!udp_length_is_valid(headerSize - 1, 64));
    assert(udp_length_is_valid(headerSize, headerSize));
    assert(udp_length_is_valid(headerSize, 1500));
    assert(udp_length_is_valid(64, 64));
    assert(!udp_length_is_valid(65, 64));
    assert(!udp_length_is_valid(65535, 1500));
    return 0;
}
'''

with tempfile.TemporaryDirectory() as directory:
    test = Path(directory) / 'test.cpp'
    test.write_text(fixture + function + main)
    binary = Path(directory) / 'test'
    subprocess.run([os.environ.get('CXX', 'c++'), '-std=c++17', '-Wall',
        '-Werror', '-fsanitize=address,undefined', '-o', str(binary),
        str(test)], check=True)
    subprocess.run([str(binary)], check=True)
