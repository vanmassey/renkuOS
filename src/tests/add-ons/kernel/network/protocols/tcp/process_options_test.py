#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Exercise the TCP option parser with valid and truncated option data.

The test compiles process_options() from tcp.cpp against a small buffer-module
fixture, then runs it with AddressSanitizer and UndefinedBehaviorSanitizer.
"""
from pathlib import Path
import os
import subprocess
import tempfile

root = (Path(os.environ['SOURCE_ROOT']) if 'SOURCE_ROOT' in os.environ
        else Path(__file__).resolve().parents[7])
source = (root / 'src/add-ons/kernel/network/protocols/tcp/tcp.cpp').read_text()
start = source.index('static void\nprocess_options(')
function = source[start:source.index('\n}\n', start) + 3]

fixture = r'''
#include <arpa/inet.h>
#include <assert.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>

typedef uint8_t uint8;
typedef uint16_t uint16;
typedef uint32_t uint32;
typedef int32_t int32;
typedef int status_t;

#define B_OK 0
#define B_ERROR -1
#define B_BAD_DATA -2
#define kMaxOptionSize 40
#define MAX_SACK_BLKS 4
#define min_c(a, b) ((a) < (b) ? (a) : (b))
#define dprintf(...) ((void)0)

enum {
 TCP_OPTION_END = 0, TCP_OPTION_NOP = 1, TCP_OPTION_MAX_SEGMENT_SIZE = 2,
 TCP_OPTION_WINDOW_SHIFT = 3, TCP_OPTION_SACK_PERMITTED = 4,
 TCP_OPTION_SACK = 5, TCP_OPTION_TIMESTAMP = 8,
};
enum {
 TCP_HAS_WINDOW_SCALE = 1 << 0, TCP_HAS_TIMESTAMPS = 1 << 1,
 TCP_SACK_PERMITTED = 1 << 2, TCP_HAS_SACK = 1 << 3,
};

struct tcp_sack { uint32 left_edge; uint32 right_edge; };
struct tcp_option {
 uint8 kind;
 uint8 length;
 union {
  uint8 window_shift;
  uint16 max_segment_size;
  struct { uint32 value; uint32 reply; } timestamp;
  tcp_sack sack[4];
 };
};
struct tcp_header { uint8 bytes[20]; };

struct tcp_segment_header {
 uint16 max_segment_size;
 uint32 options;
 uint8 window_shift;
 uint32 timestamp_value;
 uint32 timestamp_reply;
 tcp_sack sacks[MAX_SACK_BLKS];
 int sackCount;
 tcp_segment_header()
  : max_segment_size(0), options(0), window_shift(0),
    timestamp_value(0), timestamp_reply(0), sackCount(0) {}
};

struct net_buffer { uint8 bytes[64]; };
struct net_buffer_module_info {
 status_t direct_access(net_buffer*, size_t, size_t, void**) { return B_ERROR; }
 status_t read(net_buffer* buffer, size_t offset, void* out, size_t size)
 {
  memcpy(out, buffer->bytes + offset, size);
  return B_OK;
 }
};
net_buffer_module_info bufferModule;
net_buffer_module_info* gBufferModule = &bufferModule;
'''

main = r'''
static void
run(const uint8* bytes, size_t size, tcp_segment_header& segment)
{
 net_buffer buffer = {};
 memcpy(buffer.bytes + sizeof(tcp_header), bytes, size);
 process_options(segment, &buffer, size);
}

int
main()
{
 tcp_segment_header segment;

 // A non-EOL option with only its kind byte must be ignored safely.
 const uint8 truncatedHeader[] = { TCP_OPTION_MAX_SEGMENT_SIZE };
 run(truncatedHeader, sizeof(truncatedHeader), segment);
 assert(segment.max_segment_size == 0);

 // Lengths below the two-byte option header are rejected.
 const uint8 zeroLength[] = { TCP_OPTION_MAX_SEGMENT_SIZE, 0 };
 run(zeroLength, sizeof(zeroLength), segment);
 const uint8 oneLength[] = { TCP_OPTION_MAX_SEGMENT_SIZE, 1 };
 run(oneLength, sizeof(oneLength), segment);
 assert(segment.max_segment_size == 0);

 // An option claiming more bytes than remain is rejected.
 const uint8 truncatedValue[] = { TCP_OPTION_MAX_SEGMENT_SIZE, 4, 0x05 };
 run(truncatedValue, sizeof(truncatedValue), segment);
 assert(segment.max_segment_size == 0);

 // SACK must contain a two-byte header plus complete 8-byte blocks.
 const uint8 malformedSack[] = { TCP_OPTION_SACK, 3, 0x00 };
 run(malformedSack, sizeof(malformedSack), segment);
 assert((segment.options & TCP_HAS_SACK) == 0);

 // A valid MSS option is decoded in network byte order.
 const uint8 validMss[] = { TCP_OPTION_MAX_SEGMENT_SIZE, 4, 0x05, 0xb4 };
 run(validMss, sizeof(validMss), segment);
 assert(segment.max_segment_size == 1460);

 // End-of-options terminates parsing; later bytes are not interpreted.
 tcp_segment_header afterEnd;
 const uint8 endMarker[] = {
  TCP_OPTION_END, TCP_OPTION_MAX_SEGMENT_SIZE, 4, 0x05, 0xb4
 };
 run(endMarker, sizeof(endMarker), afterEnd);
 assert(afterEnd.max_segment_size == 0);

 puts("all process_options checks passed");
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
