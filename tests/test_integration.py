"""End-to-end: CIPClient against the mock processor (register, feedback, send, heartbeat)."""
import asyncio, functools, os, sys
print = functools.partial(print, flush=True)
_here = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _here)                    # tests dir (for mock_server)
import _pkg  # noqa: E402,F401

from crestron_cip.client import CIPClient
from crestron_cip import protocol as p
from mock_server import MockProcessor


async def run() -> None:
    proc = MockProcessor()
    port = await proc.start()

    got: list[tuple] = []
    client = CIPClient("127.0.0.1", ipid=0x0A, port=port, use_ssl=False,
                       on_feedback=lambda k, j, v, o=0: got.append((k, j, v)))
    await client.connect(timeout=5.0)
    assert client.connected, "client did not register"
    assert proc.registered_ipid == 0x0A, f"server saw ipid {proc.registered_ipid}"
    # Registered as Crestron's XPanel registers, so the processor offers a
    # panel's capabilities (and xpanel_mux can pass them on).
    assert proc.registered_body == bytes.fromhex("00000000000a40fffff101"), proc.registered_body.hex()
    print(f"  ok  registered (server saw IPID 0x{proc.registered_ipid:02X})")

    await asyncio.sleep(0.2)
    assert client.digital.get(45) is True, client.digital
    assert client.analog.get(5) == 32000, client.analog
    assert client.serial.get(3) == "Zone 1", client.serial
    assert ("digital", 45, True) in got and ("analog", 5, 32000) in got and ("serial", 3, "Zone 1") in got
    print(f"  ok  feedback parsed: digital[45]={client.digital[45]} analog[5]={client.analog[5]} serial[3]={client.serial[3]!r}")

    await client.set_digital(12, True)
    await client.set_analog(7, 12345)
    await client.set_serial(9, "Hello")
    await asyncio.sleep(0.2)
    assert ("digital", 12, True) in proc.received_joins, proc.received_joins
    assert ("analog", 7, 12345) in proc.received_joins, proc.received_joins
    assert ("serial", 9, "Hello") in proc.received_joins, proc.received_joins
    print(f"  ok  server received our joins: {proc.received_joins}")

    await client.close()
    await proc.stop()
    print("PASSED end-to-end integration (register, feedback, send, heartbeat-reply)")


if __name__ == "__main__":
    try:
        asyncio.run(asyncio.wait_for(run(), 15))
    except asyncio.TimeoutError:
        print("TIMEOUT: run() did not finish in 15s")
        sys.exit(2)
