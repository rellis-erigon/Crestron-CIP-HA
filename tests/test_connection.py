"""The client refuses an undefined IPID and notices a processor that goes silent."""
import asyncio, functools, os, sys
print = functools.partial(print, flush=True)
_here = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _here)
import _pkg  # noqa: E402,F401

from crestron_cip.client import CIPClient
from mock_server import MockProcessor


async def undefined_ipid_is_refused() -> None:
    proc = MockProcessor()
    proc.registration_reply = bytes([0xFF, 0xFF, 0x02])
    port = await proc.start()
    client = CIPClient("127.0.0.1", ipid=0x0A, port=port)
    try:
        await client.connect(timeout=5.0)
    except ConnectionRefusedError as exc:
        assert "not defined" in str(exc), exc
    else:
        raise AssertionError("connected to an IPID the program does not define")
    assert not client.connected
    await client.close()
    await proc.stop()
    print("  ok  ff ff 02 is a refusal, not a connection")


async def silent_processor_is_dropped() -> None:
    proc = MockProcessor()
    proc.answer_heartbeats = False
    port = await proc.start()
    dropped = asyncio.Event()
    client = CIPClient("127.0.0.1", ipid=0x0A, port=port, heartbeat_interval=0.1,
                       on_disconnect=dropped.set)
    await client.connect(timeout=5.0)
    await asyncio.wait_for(dropped.wait(), 3)
    await client.close()
    await proc.stop()
    print("  ok  a processor that stops answering is reported as dropped")


async def reconnects_after_a_drop() -> None:
    proc = MockProcessor()
    port = await proc.start()
    client = CIPClient("127.0.0.1", ipid=0x0A, port=port)
    await client.connect(timeout=5.0)
    await client.close()
    await client.connect(timeout=5.0)
    assert client.connected
    await asyncio.sleep(0.2)
    assert client.digital.get(45) is True, client.digital
    await client.close()
    await proc.stop()
    print("  ok  the same client connects again after closing")


async def run() -> None:
    await undefined_ipid_is_refused()
    await silent_processor_is_dropped()
    await reconnects_after_a_drop()
    print("PASSED connection tests")


if __name__ == "__main__":
    asyncio.run(asyncio.wait_for(run(), 20))
