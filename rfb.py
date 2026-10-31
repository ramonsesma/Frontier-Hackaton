"""Cliente RFB (VNC) minimo sobre websocket (websockify). Solo encoding Raw.

Respeta lo observado en el escritorio:
- la pantalla tarda hasta ~1 s en repintar tras una accion (capture() espera)
- un clic con <100 ms entre bajar y soltar se ignora (se mantiene ~150 ms)
- escribir sobre un campo con texto concatena (el agente usa ctrl+a antes)
"""
import asyncio
import io
import struct

import websockets
from Crypto.Cipher import DES
from PIL import Image

KEYSYMS = {
    "return": 0xFF0D, "enter": 0xFF0D, "tab": 0xFF09, "escape": 0xFF1B, "esc": 0xFF1B,
    "backspace": 0xFF08, "delete": 0xFFFF, "home": 0xFF50, "left": 0xFF51, "up": 0xFF52,
    "right": 0xFF53, "down": 0xFF54, "page_up": 0xFF55, "pageup": 0xFF55,
    "page_down": 0xFF56, "pagedown": 0xFF56, "end": 0xFF57, "insert": 0xFF63,
    "ctrl": 0xFFE3, "control": 0xFFE3, "shift": 0xFFE1, "alt": 0xFFE9, "super": 0xFFEB,
    "space": 0x20,
}
for _i in range(1, 13):
    KEYSYMS[f"f{_i}"] = 0xFFBD + _i


def keysym_for_char(ch: str) -> int:
    if ch == "\n":
        return 0xFF0D
    if ch == "\t":
        return 0xFF09
    o = ord(ch)
    if o < 0x100:
        return o
    return 0x01000000 + o


def keysym_for_name(name: str) -> int:
    n = name.strip().lower()
    if n in KEYSYMS:
        return KEYSYMS[n]
    if len(n) == 1:
        return keysym_for_char(n)
    raise ValueError(f"tecla desconocida: {name}")


class RFBClient:
    def __init__(self, url: str, password: str):
        self.url = url
        self.password = password
        self.ws = None
        self.width = 0
        self.height = 0
        self.fb: Image.Image | None = None
        self._buf = bytearray()
        self._chunks: asyncio.Queue = asyncio.Queue()
        self._reader_task = None
        self._recv_task = None
        self._update_event = asyncio.Event()
        self._closed = False
        self._mouse = (0, 0)
        self._buttons = 0

    # ---------- bajo nivel ----------
    async def _pump(self):
        try:
            async for msg in self.ws:
                if isinstance(msg, str):
                    msg = msg.encode("latin-1", "ignore")
                await self._chunks.put(bytes(msg))
        except Exception:
            pass
        finally:
            self._closed = True
            await self._chunks.put(None)

    async def _read(self, n: int) -> bytes:
        while len(self._buf) < n:
            chunk = await self._chunks.get()
            if chunk is None:
                raise ConnectionError("escritorio desconectado")
            self._buf.extend(chunk)
        out = bytes(self._buf[:n])
        del self._buf[:n]
        return out

    async def _send(self, data: bytes):
        await self.ws.send(data)

    # ---------- conexion ----------
    async def connect(self):
        try:
            self.ws = await websockets.connect(
                self.url, subprotocols=["binary"], max_size=None, open_timeout=15,
                ping_interval=None)
        except Exception:
            self.ws = await websockets.connect(
                self.url, max_size=None, open_timeout=15, ping_interval=None)
        self._recv_task = asyncio.create_task(self._pump())

        version = await self._read(12)
        await self._send(b"RFB 003.008\n")
        if not version.startswith(b"RFB"):
            raise ConnectionError("no es un servidor RFB")

        n = (await self._read(1))[0]
        if n == 0:
            ln = struct.unpack(">I", await self._read(4))[0]
            reason = (await self._read(ln)).decode("utf-8", "replace")
            raise ConnectionError(f"servidor rechazo la conexion: {reason}")
        types = list(await self._read(n))
        if 2 in types:
            await self._send(bytes([2]))
            challenge = await self._read(16)
            key = self.password.encode("latin-1", "ignore")[:8].ljust(8, b"\0")
            key = bytes(int(f"{b:08b}"[::-1], 2) for b in key)  # bits invertidos (VNC)
            des = DES.new(key, DES.MODE_ECB)
            await self._send(des.encrypt(challenge))
        elif 1 in types:
            await self._send(bytes([1]))
        else:
            raise ConnectionError(f"seguridad no soportada: {types}")
        result = struct.unpack(">I", await self._read(4))[0]
        if result != 0:
            raise ConnectionError("autenticacion VNC fallida")

        await self._send(bytes([1]))  # ClientInit: shared
        self.width, self.height = struct.unpack(">HH", await self._read(4))
        await self._read(16)  # pixel format del servidor (lo reemplazamos)
        nl = struct.unpack(">I", await self._read(4))[0]
        await self._read(nl)

        # 32 bpp, little endian, true color, R<<16 G<<8 B => bytes B,G,R,X
        pf = struct.pack(">BBBBHHHBBB3x", 32, 24, 0, 1, 255, 255, 255, 16, 8, 0)
        await self._send(b"\x00\x00\x00\x00" + pf)
        await self._send(struct.pack(">BxHi", 2, 1, 0))  # SetEncodings: Raw
        self.fb = Image.new("RGB", (self.width, self.height))
        self._reader_task = asyncio.create_task(self._reader())
        await self._request(incremental=False)
        await asyncio.wait_for(self._update_event.wait(), timeout=10)

    async def _request(self, incremental: bool):
        await self._send(struct.pack(">BBHHHH", 3, 1 if incremental else 0,
                                     0, 0, self.width, self.height))

    async def _reader(self):
        try:
            while True:
                t = (await self._read(1))[0]
                if t == 0:
                    await self._read(1)
                    nrects = struct.unpack(">H", await self._read(2))[0]
                    for _ in range(nrects):
                        x, y, w, h, enc = struct.unpack(">HHHHi", await self._read(12))
                        if enc != 0:
                            raise ConnectionError(f"encoding no soportado: {enc}")
                        data = await self._read(w * h * 4)
                        if w and h:
                            img = Image.frombytes("RGB", (w, h), data, "raw", "BGRX")
                            self.fb.paste(img, (x, y))
                    self._update_event.set()
                elif t == 1:
                    await self._read(1)
                    _first, num = struct.unpack(">HH", await self._read(4))
                    await self._read(num * 6)
                elif t == 2:
                    pass
                elif t == 3:
                    await self._read(3)
                    ln = struct.unpack(">I", await self._read(4))[0]
                    await self._read(ln)
                else:
                    raise ConnectionError(f"mensaje RFB desconocido: {t}")
        except Exception:
            self._closed = True
            self._update_event.set()

    # ---------- pantalla ----------
    async def screenshot(self, settle: float = 1.0) -> Image.Image:
        """Espera el repintado y devuelve una captura fresca."""
        if self._closed:
            raise ConnectionError("escritorio desconectado")
        await asyncio.sleep(settle)
        self._update_event.clear()
        await self._request(incremental=False)
        try:
            await asyncio.wait_for(self._update_event.wait(), timeout=5)
        except asyncio.TimeoutError:
            pass
        if self._closed:
            raise ConnectionError("escritorio desconectado")
        return self.fb.copy()

    def png(self, img: Image.Image) -> bytes:
        buf = io.BytesIO()
        img.save(buf, "PNG", optimize=False)
        return buf.getvalue()

    # ---------- entrada ----------
    def _clamp(self, x, y):
        return (max(0, min(self.width - 1, int(x))), max(0, min(self.height - 1, int(y))))

    async def _pointer(self, x, y, mask):
        x, y = self._clamp(x, y)
        self._mouse = (x, y)
        self._buttons = mask
        await self._send(struct.pack(">BBHH", 5, mask, x, y))

    async def move(self, x, y):
        await self._pointer(x, y, 0)

    async def click(self, x, y, button: int = 1, count: int = 1):
        mask = 1 << (button - 1)
        await self._pointer(x, y, 0)
        await asyncio.sleep(0.05)
        for i in range(count):
            await self._pointer(x, y, mask)
            await asyncio.sleep(0.15)  # >100 ms o se ignora
            await self._pointer(x, y, 0)
            if i < count - 1:
                await asyncio.sleep(0.1)

    async def scroll(self, x, y, direction: str, amount: int = 3):
        mask = 8 if direction == "up" else 16
        await self._pointer(x, y, 0)
        for _ in range(max(1, amount)):
            await self._pointer(x, y, mask)
            await asyncio.sleep(0.05)
            await self._pointer(x, y, 0)
            await asyncio.sleep(0.05)

    async def _key(self, keysym: int, down: bool):
        await self._send(struct.pack(">BBxxI", 4, 1 if down else 0, keysym))

    async def press(self, combo: str):
        """'Return', 'ctrl+a', 'shift+Tab'..."""
        parts = [p for p in combo.replace(" ", "").split("+") if p]
        if combo.strip().endswith("+"):
            parts = parts[:-1] + ["+"]
        syms = [keysym_for_name(p) for p in parts]
        for s in syms:
            await self._key(s, True)
            await asyncio.sleep(0.03)
        await asyncio.sleep(0.05)
        for s in reversed(syms):
            await self._key(s, False)
            await asyncio.sleep(0.03)

    async def type_text(self, text: str, delay: float = 0.02):
        for ch in text:
            s = keysym_for_char(ch)
            await self._key(s, True)
            await asyncio.sleep(delay)
            await self._key(s, False)
            await asyncio.sleep(delay)

    async def close(self):
        self._closed = True
        for t in (self._reader_task, self._recv_task):
            if t:
                t.cancel()
        try:
            if self.ws:
                await self.ws.close()
        except Exception:
            pass
