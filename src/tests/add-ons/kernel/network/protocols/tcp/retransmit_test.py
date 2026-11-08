#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Exercise the retransmission state machine directly from TCPEndpoint.cpp.

The fixture supplies only the endpoint collaborators needed by _Retransmit().
It checks the SYN-ACK retry budget, timer re-arming, and established-state
exponential backoff without needing a running network stack.
"""
from pathlib import Path
import os
import subprocess
import tempfile

root = (Path(os.environ['SOURCE_ROOT']) if 'SOURCE_ROOT' in os.environ
        else Path(__file__).resolve().parents[7])
source = (root / 'src/add-ons/kernel/network/protocols/tcp/'
        'TCPEndpoint.cpp').read_text()
start = source.index('bool\nTCPEndpoint::_Retransmit()')
end = source.index('\n}\n\n\nvoid\nTCPEndpoint::_UpdateRoundTripTime', start) + 3
function = source[start:end]

fixture = r"""
#include <assert.h>
#include <stdint.h>
#include <stdio.h>

typedef uint32_t uint32;
typedef int32_t int32;
typedef int64_t bigtime_t;
typedef int status_t;

#define B_OK 0
#define ECONNREFUSED 111
#define TCP_SYN_RETRANSMIT_TIMEOUT 1000000
#define TCP_MAX_RETRANSMIT_TIMEOUT 60000000
#define TCP_MAX_SYN_ACKNOWLEDGE_RETRANSMITS 8
#define FLAG_RECOVERY 0x01

#define TRACE(...) ((void)0)
#define T(...) ((void)0)

enum tcp_state {
    CLOSED,
    LISTEN,
    SYNCHRONIZE_SENT,
    SYNCHRONIZE_RECEIVED,
    ESTABLISHED
};

class tcp_sequence {
public:
    tcp_sequence(uint32 value = 0) : fValue(value) {}
    uint32 Number() const { return fValue; }
    tcp_sequence operator=(uint32 value)
        { fValue = value; return *this; }
private:
    uint32 fValue;
};

struct net_timer {};
struct net_socket {};

struct SocketModule {
    bool acquire_socket(net_socket*) { acquireCount++; return acquireResult; }
    int acquireCount = 0;
    bool acquireResult = true;
};

struct StackModule {
    void set_timer(net_timer*, bigtime_t timeout)
        { timerCount++; lastTimeout = timeout; }
    int timerCount = 0;
    bigtime_t lastTimeout = 0;
};

static SocketModule socketModule;
static StackModule stackModule;
static SocketModule* gSocketModule = &socketModule;
static StackModule* gStackModule = &stackModule;

class TCPEndpoint {
public:
    bool _Retransmit();
    void _ResetSlowStart() { resetCount++; }
    status_t _SendAcknowledge()
        { acknowledgeCount++; return acknowledgeStatus; }
    status_t _RetransmitSynchronizeAcknowledge()
        { synAckCount++; return synAckStatus; }
    status_t _SendQueued()
        { queuedSendCount++; return B_OK; }
    void _HandleReset(int error)
        { resetError = error; }

    tcp_state fState = ESTABLISHED;
    uint32 fRetransmitInitialCount = 0;
    bigtime_t fRetransmitTimeout = 1000000;
    uint32 fSendMaxSegmentSize = 1460;
    uint32 fCongestionWindow = 10000;
    uint32 fDuplicateAcknowledgeCount = 3;
    uint32 fFlags = FLAG_RECOVERY;
    tcp_sequence fSendNext = tcp_sequence(200);
    tcp_sequence fSendUnacknowledged = tcp_sequence(100);
    uint32 fRecover = 0;
    net_timer fRetransmitTimer;
    net_socket socket;

    int resetCount = 0;
    int acknowledgeCount = 0;
    int synAckCount = 0;
    int queuedSendCount = 0;
    int resetError = 0;
    status_t acknowledgeStatus = B_OK;
    status_t synAckStatus = B_OK;
};
"""

main = r"""
int
main()
{
    // A half-open passive connection gives up once its SYN-ACK budget
    // is exhausted. The timer callback owns a socket reference only
    // when _Retransmit() returns true.
    TCPEndpoint endpoint;
    endpoint.fState = SYNCHRONIZE_RECEIVED;
    endpoint.fRetransmitInitialCount = TCP_MAX_SYN_ACKNOWLEDGE_RETRANSMITS;
    assert(endpoint._Retransmit());
    assert(socketModule.acquireCount == 1);
    assert(endpoint.resetError == ECONNREFUSED);
    assert(endpoint.synAckCount == 0);
    assert(stackModule.timerCount == 0);

    // Before the budget is exhausted, a SYN-ACK is retransmitted and
    // the timer is re-armed. The initial timeout remains one second.
    socketModule = SocketModule();
    stackModule = StackModule();
    endpoint = TCPEndpoint();
    endpoint.fState = SYNCHRONIZE_RECEIVED;
    endpoint.fRetransmitInitialCount = 2;
    assert(!endpoint._Retransmit());
    assert(endpoint.synAckCount == 1);
    assert(endpoint.fRetransmitInitialCount == 3);
    assert(stackModule.timerCount == 1);
    assert(stackModule.lastTimeout == TCP_SYN_RETRANSMIT_TIMEOUT);

    // An established connection backs off its retransmission timeout,
    // resets congestion recovery state, and retransmits queued data.
    stackModule = StackModule();
    endpoint = TCPEndpoint();
    endpoint.fState = ESTABLISHED;
    endpoint.fRetransmitTimeout = 40000000;
    assert(!endpoint._Retransmit());
    assert(endpoint.resetCount == 1);
    assert(endpoint.queuedSendCount == 1);
    assert(endpoint.fDuplicateAcknowledgeCount == 0);
    assert((endpoint.fFlags & FLAG_RECOVERY) == 0);
    assert(endpoint.fRetransmitTimeout == TCP_MAX_RETRANSMIT_TIMEOUT);
    assert(stackModule.lastTimeout == 0);

    // SYN-SENT retries the active-open SYN and re-arms its timer.
    stackModule = StackModule();
    endpoint = TCPEndpoint();
    endpoint.fState = SYNCHRONIZE_SENT;
    assert(!endpoint._Retransmit());
    assert(endpoint.acknowledgeCount == 1);
    assert(endpoint.fRetransmitInitialCount == 1);
    assert(stackModule.timerCount == 1);
    assert(stackModule.lastTimeout == TCP_SYN_RETRANSMIT_TIMEOUT);

    puts("all TCP retransmission checks passed");
    return 0;
}
"""

with tempfile.TemporaryDirectory() as directory:
    test = Path(directory) / 'test.cpp'
    test.write_text(fixture + function + main)
    binary = Path(directory) / 'test'
    subprocess.run([os.environ.get('CXX', 'c++'), '-std=c++17', '-Wall',
        '-Werror', '-fsanitize=address,undefined', '-o', str(binary),
        str(test)], check=True)
    subprocess.run([str(binary)], check=True)
