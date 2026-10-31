"""Prueba local: servidor RFB falso sobre websocket para validar handshake, auth y framebuffer."""
import asyncio
import struct

import websockets
from Crypto.Cipher import DES

from rfb import RFBClient

W, H, PASSWORD = 64, 32, "secreto1"
events = []


async def handler(ws):
    await ws.send(b"RFB 003.008\n")
    await ws.recv()
    await ws.send(bytes([1, 2]))
    assert (await ws.recv()) == bytes([2])
    challenge = bytes(range(16))
    await ws.send(challenge)
    resp = await ws.recv()
    key = bytes(int(f"{b:08b}"[::-1], 2) for b in PASSWORD.encode().ljust(8, b"\0"))
    ok = DES.new(key, DES.MODE_ECB).encrypt(challenge) == resp
    await ws.send(struct.pack(">I", 0 if ok else 1))
    if not ok:
        return
    await ws.recv()  # ClientInit
    await ws.send(struct.pack(">HH", W, H) + b"\0" * 16 + struct.pack(">I", 4) + b"test")
    async for msg in ws:
        t = msg[0]
        if t == 3:  # FramebufferUpdateRequest -> pantalla roja (BGRX)
            px = bytes([0, 0, 255, 0]) * (W * H)
            await ws.send(struct.pack(">BxHHHHHi", 0, 1, 0, 0, W, H, 0) + px)
        elif t in (4, 5):
            events.append(bytes(msg))


async def main():
    async with websockets.serve(handler, "127.0.0.1", 8765, subprotocols=["binary"]):
        c = RFBClient("ws://127.0.0.1:8765", PASSWORD)
        await c.connect()
        img = await c.screenshot(settle=0.1)
        assert img.size == (W, H) and img.getpixel((3, 3)) == (255, 0, 0), img.getpixel((3, 3))
        await c.click(10, 10)
        await c.type_text("Ñá")
        await c.press("ctrl+a")
        await asyncio.sleep(0.2)
        kinds = [e[0] for e in events]
        assert kinds.count(5) >= 3 and kinds.count(4) >= 8, kinds
        await c.close()
        bad = RFBClient("ws://127.0.0.1:8765", "mala")
        try:
            await bad.connect()
            raise SystemExit("deberia fallar la auth")
        except ConnectionError:
            pass
        print("RFB OK")


asyncio.run(main())
