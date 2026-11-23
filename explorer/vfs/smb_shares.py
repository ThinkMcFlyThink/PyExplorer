"""
list_shares - ask an SMB server (TrueNAS, Windows, Samba, a NAS box...) which
shares it offers, so that opening smb://server/ shows them as folders.

The ``smbprotocol`` library handles files but has no "list shares" function,
so this module makes the standard call itself - the same one Windows
Explorer and ``smbclient -L`` use:

    1. open the special pipe  \\\\server\\IPC$\\srvsvc
    2. send a DCE/RPC "bind" to the Server Service interface
    3. send NetrShareEnum (operation 15), level 1 = name + type + comment
    4. decode the reply

DCE/RPC data uses "NDR" encoding: little-endian numbers aligned to 4 bytes,
strings as (max count, offset, actual count, UTF-16 text), and pointers as a
4-byte "referent id" with the pointed-to data stored later. The helpers below
only implement the small part of that needed here.
"""
from __future__ import annotations

import struct
import uuid
from typing import List, Tuple

# Interface ids (fixed numbers defined by Microsoft).
SRVSVC_UUID = uuid.UUID("4b324fc8-1670-01d3-1278-5a47bf6ee188")   # Server Service, version 3.0
NDR_UUID = uuid.UUID("8a885d04-1ceb-11c9-9fe8-08002b104860")      # NDR transfer syntax, version 2

PTYPE_REQUEST, PTYPE_RESPONSE, PTYPE_FAULT, PTYPE_BIND, PTYPE_BIND_ACK = 0, 2, 3, 11, 12
PFC_FIRST, PFC_LAST = 0x01, 0x02
MAX_FRAG = 4280
OP_NETR_SHARE_ENUM = 15

STYPE_DISKTREE = 0
STYPE_MASK = 0x0FFFFFFF
STYPE_SPECIAL = 0x80000000          # admin/hidden shares like C$ and IPC$


def _header(ptype: int, body: bytes, call_id: int) -> bytes:
    # version 5.0, flags first+last, little-endian data representation
    return struct.pack("<BBBB4sHHI", 5, 0, ptype, PFC_FIRST | PFC_LAST, b"\x10\x00\x00\x00",
                       16 + len(body), 0, call_id) + body


def _bind_pdu() -> bytes:
    body = struct.pack("<HHI", MAX_FRAG, MAX_FRAG, 0)              # max xmit/recv frag, assoc group
    body += struct.pack("<BBH", 1, 0, 0)                            # one presentation context
    body += struct.pack("<HBB", 0, 1, 0)                            # context id 0, one transfer syntax
    body += SRVSVC_UUID.bytes_le + struct.pack("<HH", 3, 0)         # abstract syntax: srvsvc v3.0
    body += NDR_UUID.bytes_le + struct.pack("<I", 2)                # transfer syntax: NDR v2
    return _header(PTYPE_BIND, body, 1)


def _ndr_string(text: str) -> bytes:
    """A conformant varying UTF-16 string, padded to 4 bytes."""
    data = (text + "\x00").encode("utf-16-le")
    count = len(text) + 1
    out = struct.pack("<III", count, 0, count) + data
    return out + b"\x00" * (-len(out) % 4)


def _share_enum_request(server: str) -> bytes:
    stub = struct.pack("<I", 0x00020000) + _ndr_string("\\\\" + server)  # [unique] ServerName
    stub += struct.pack("<II", 1, 1)               # InfoStruct.Level = 1, union switch = 1
    stub += struct.pack("<I", 0x00020004)          # pointer to SHARE_INFO_1_CONTAINER
    stub += struct.pack("<II", 0, 0)               # container: EntriesRead = 0, Buffer = NULL
    stub += struct.pack("<I", 0xFFFFFFFF)          # PreferedMaximumLength: "as much as possible"
    stub += struct.pack("<I", 0)                   # ResumeHandle = NULL
    body = struct.pack("<IHH", len(stub), 0, OP_NETR_SHARE_ENUM) + stub
    return _header(PTYPE_REQUEST, body, 2)


class _Reader:
    """Reads NDR values from the response, keeping 4-byte alignment."""

    def __init__(self, data: bytes) -> None:
        self.data, self.pos = data, 0

    def u32(self) -> int:
        value = struct.unpack_from("<I", self.data, self.pos)[0]
        self.pos += 4
        return value

    def string(self) -> str:
        _max, _offset, actual = struct.unpack_from("<III", self.data, self.pos)
        self.pos += 12
        raw = self.data[self.pos:self.pos + actual * 2]
        self.pos += actual * 2
        self.pos += -self.pos % 4
        return raw.decode("utf-16-le", "replace").rstrip("\x00")


def parse_share_enum_response(stub: bytes) -> List[Tuple[str, int, str]]:
    """[(name, type, comment)] from the NetrShareEnum reply's stub data."""
    r = _Reader(stub)
    r.u32()                       # Level
    r.u32()                       # union switch
    if r.u32() == 0:              # container pointer
        return []
    count = r.u32()               # EntriesRead
    if r.u32() == 0:              # Buffer pointer
        return []
    r.u32()                       # array max count
    fixed = [(r.u32(), r.u32(), r.u32()) for _ in range(count)]   # (name ptr, type, comment ptr)
    shares = []
    for name_ptr, share_type, comment_ptr in fixed:
        name = r.string() if name_ptr else ""
        comment = r.string() if comment_ptr else ""
        shares.append((name, share_type, comment))
    return shares


def _stub_from_responses(fragments: List[bytes]) -> bytes:
    stub = b""
    for frag in fragments:
        ptype = frag[2]
        if ptype == PTYPE_FAULT:
            status = struct.unpack_from("<I", frag, 24)[0]
            raise OSError(f"The server refused to list its shares (error 0x{status:08x})")
        if ptype != PTYPE_RESPONSE:
            raise OSError("Unexpected reply while listing shares")
        frag_len, auth_len = struct.unpack_from("<HH", frag, 8)
        stub += frag[24:frag_len - (auth_len + 8 if auth_len else 0)]
    return stub


def list_shares(session, server: str) -> List[Tuple[str, str]]:
    """[(share name, comment)] of the normal disk shares (no printers, no IPC$/C$).
    ``session`` is an authenticated smbprotocol Session (from smbclient.register_session)."""
    from smbprotocol.open import (CreateDisposition, CreateOptions, FilePipePrinterAccessMask,
                                  ImpersonationLevel, Open, ShareAccess)
    from smbprotocol.tree import TreeConnect

    tree = TreeConnect(session, rf"\\{server}\IPC$")
    tree.connect()
    pipe = Open(tree, "srvsvc")
    try:
        pipe.create(ImpersonationLevel.Impersonation,
                    FilePipePrinterAccessMask.FILE_READ_DATA | FilePipePrinterAccessMask.FILE_WRITE_DATA,
                    0, ShareAccess.FILE_SHARE_READ | ShareAccess.FILE_SHARE_WRITE,
                    CreateDisposition.FILE_OPEN, CreateOptions.FILE_NON_DIRECTORY_FILE)
        pipe.write(_bind_pdu(), 0)
        ack = pipe.read(0, MAX_FRAG)
        if ack[2] != PTYPE_BIND_ACK:
            raise OSError("The server does not offer share listing")
        pipe.write(_share_enum_request(server), 0)
        fragments = []
        while True:                                   # replies can be split into several fragments
            frag = pipe.read(0, MAX_FRAG)
            fragments.append(frag)
            if frag[3] & PFC_LAST:
                break
        shares = parse_share_enum_response(_stub_from_responses(fragments))
    finally:
        try:
            pipe.close(get_attributes=False)
        except Exception:  # noqa: BLE001
            pass
        try:
            tree.disconnect()
        except Exception:  # noqa: BLE001
            pass
    return sorted(((name, comment) for name, kind, comment in shares
                   if (kind & STYPE_MASK) == STYPE_DISKTREE and not kind & STYPE_SPECIAL and not name.endswith("$")),
                  key=lambda s: s[0].lower())
