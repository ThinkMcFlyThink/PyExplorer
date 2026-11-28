"""
Programs and libraries - Linux ELF, Windows PE (.exe / .dll), macOS Mach-O,
AppImages and Java class files: what CPU it is for, 32 or 64-bit, program or
library, console or windowed, .NET or native, how it is linked.

Only the first few kilobytes are read, with ``struct``.
"""
from __future__ import annotations

import struct
import time
from datetime import datetime
from typing import Optional

from .base import Info, InfoProvider, Source

ELF_MACHINES = {3: "x86 (32-bit)", 0x3E: "x86-64", 0x28: "ARM (32-bit)", 0xB7: "ARM64 (AArch64)", 0xF3: "RISC-V",
                8: "MIPS", 0x14: "PowerPC", 0x15: "PowerPC 64", 0x2B: "SPARC V9", 0x16: "IBM S/390", 0x102: "LoongArch"}
ELF_TYPES = {1: "Object file", 2: "Program", 3: "Shared library", 4: "Core dump"}
PE_MACHINES = {0x14C: "x86 (32-bit)", 0x8664: "x86-64", 0xAA64: "ARM64", 0x1C4: "ARM (32-bit)", 0x200: "Itanium",
               0x5064: "RISC-V 64"}
PE_SUBSYSTEMS = {1: "Driver / native", 2: "Windowed (GUI)", 3: "Console", 9: "Windows CE", 10: "EFI application",
                 11: "EFI boot driver", 12: "EFI runtime driver", 14: "Xbox"}
MACHO_CPUS = {7: "x86 (32-bit)", 0x01000007: "x86-64", 12: "ARM (32-bit)", 0x0100000C: "ARM64 (Apple silicon)",
              18: "PowerPC", 0x01000012: "PowerPC 64"}
MACHO_TYPES = {1: "Object file", 2: "Program", 6: "Dynamic library", 8: "Bundle / plug-in", 4: "Core dump"}
JAVA_VERSIONS = {45: "1.1", 46: "1.2", 47: "1.3", 48: "1.4", 49: "5", 50: "6", 51: "7", 52: "8"}
BINARY_EXTS = {"exe", "dll", "sys", "scr", "ocx", "efi", "cpl", "drv", "so", "o", "ko", "elf", "bin", "run",
               "appimage", "out", "dylib", "bundle", "axf", "prx", "node", "pyd", "mod", "class", "com", "msix"}


def read_elf(head: bytes) -> Info:
    bits = 64 if head[4] == 2 else 32
    endian = "<" if head[5] == 1 else ">"
    e_type, machine = struct.unpack(endian + "HH", head[16:20])
    info = Info(kind="Linux / Unix program")
    s = info.section("Program")
    if head[8:11] == b"AI\x02":
        info.kind = "AppImage"
        s.add("Format", "AppImage (type 2) — a self-contained app; make it executable to run it")
    s.add("CPU", ELF_MACHINES.get(machine, f"machine {machine:#x}"))
    s.add("Bits", f"{bits}-bit, {'little' if endian == '<' else 'big'}-endian")
    if bits == 64:
        phoff = struct.unpack(endian + "Q", head[32:40])[0]
        phentsize, phnum = struct.unpack(endian + "HH", head[54:58])
    else:
        phoff = struct.unpack(endian + "I", head[28:32])[0]
        phentsize, phnum = struct.unpack(endian + "HH", head[42:46])
    interp = ""
    dynamic = False
    for i in range(min(phnum, 64)):
        rec = head[phoff + i * phentsize: phoff + (i + 1) * phentsize]
        if len(rec) < 32:
            break
        p_type = struct.unpack(endian + "I", rec[:4])[0]
        if p_type == 2:
            dynamic = True
        elif p_type == 3:                                         # PT_INTERP
            if bits == 64:
                off, size = struct.unpack(endian + "Q", rec[8:16])[0], struct.unpack(endian + "Q", rec[32:40])[0]
            else:
                off, size = struct.unpack(endian + "I", rec[4:8])[0], struct.unpack(endian + "I", rec[16:20])[0]
            interp = head[off:off + size].rstrip(b"\0").decode("latin-1", "replace")
    kind = ELF_TYPES.get(e_type, f"type {e_type}")
    if e_type == 3 and interp:
        kind = "Program (position-independent)"
    s.add("Type", kind)
    if e_type in (2, 3):
        s.add("Linking", f"Dynamic (loader {interp})" if interp else "Dynamic" if dynamic else "Static — needs no libraries")
    return info


def read_pe(head: bytes, read_more) -> Optional[Info]:
    pe = struct.unpack("<I", head[0x3C:0x40])[0]
    if pe + 24 > len(head):
        head = read_more(pe + 4096)
    if head[pe:pe + 4] != b"PE\0\0":
        info = Info(kind="DOS program")
        info.section("Program").add("Format", "MS-DOS (MZ)")
        return info
    machine, _sections, stamp = struct.unpack("<HHI", head[pe + 4:pe + 12])
    opt_size, chars = struct.unpack("<HH", head[pe + 20:pe + 24])
    opt = pe + 24
    magic = struct.unpack("<H", head[opt:opt + 2])[0]
    is64 = magic == 0x20B
    subsystem = struct.unpack("<H", head[opt + 68:opt + 70])[0] if opt_size >= 70 else 0
    dirs = opt + (112 if is64 else 96)
    clr = struct.unpack("<I", head[dirs + 14 * 8:dirs + 14 * 8 + 4])[0] if opt_size >= (112 if is64 else 96) + 15 * 8 else 0
    is_dll = bool(chars & 0x2000)
    info = Info(kind="Windows " + ("library (DLL)" if is_dll else "program"))
    s = info.section("Program")
    s.add("CPU", PE_MACHINES.get(machine, f"machine {machine:#x}"))
    s.add("Bits", "64-bit (PE32+)" if is64 else "32-bit (PE32)")
    s.add("Type", "Library (DLL)" if is_dll else "Program")
    s.add("Subsystem", PE_SUBSYSTEMS.get(subsystem, ""))
    s.add("Runtime", ".NET (managed code)" if clr else "Native")
    if 788_918_400 < stamp < time.time() + 365 * 86400:             # 1995 .. next year (else: a build hash)
        s.add("Built", datetime.fromtimestamp(stamp).strftime("%Y-%m-%d %H:%M"))
    return info


def read_macho(head: bytes) -> Optional[Info]:
    magic = head[:4]
    if magic in (b"\xca\xfe\xba\xbe", b"\xbe\xba\xfe\xca"):
        count = struct.unpack(">I", head[4:8])[0]
        if count > 30:
            return None                                              # a Java class file, not a fat binary
        cpus = [MACHO_CPUS.get(struct.unpack(">i", head[8 + i * 20:12 + i * 20])[0], "?") for i in range(count)]
        info = Info(kind="macOS program")
        info.section("Program").add("Format", "Universal binary").add("CPU", ", ".join(cpus))
        return info
    endian = "<" if magic in (b"\xce\xfa\xed\xfe", b"\xcf\xfa\xed\xfe") else ">"
    cpu, _sub, ftype = struct.unpack(endian + "iII", head[4:16])
    info = Info(kind="macOS " + ("library" if ftype == 6 else "program"))
    s = info.section("Program")
    s.add("CPU", MACHO_CPUS.get(cpu, f"cpu {cpu:#x}"))
    s.add("Bits", "64-bit" if magic in (b"\xcf\xfa\xed\xfe", b"\xfe\xed\xfa\xcf") else "32-bit")
    s.add("Type", MACHO_TYPES.get(ftype, f"type {ftype}"))
    return info


def read_class(head: bytes) -> Info:
    major = struct.unpack(">H", head[6:8])[0]
    info = Info(kind="Java class")
    version = JAVA_VERSIONS.get(major, str(major - 44) if major > 52 else str(major))
    info.section("Program").add("Needs Java", f"{version} or newer (class version {major})")
    return info


class BinaryProvider(InfoProvider):
    name = "Programs and libraries"
    exts = frozenset(BINARY_EXTS)
    priority = 30
    local_only = False

    def matches(self, entry) -> bool:
        if entry.is_dir:
            return False
        return entry.ext in self.exts or ".so." in entry.name or (entry.category == "other" and not entry.ext) \
            or entry.ext.isdigit()

    def extract(self, src: Source) -> Optional[Info]:
        head = src.head(16384)
        if head[:4] == b"\x7fELF":
            return read_elf(head)
        if head[:2] == b"MZ" and len(head) >= 0x40:
            return read_pe(head, lambda n: src.head(n))
        if head[:4] in (b"\xfe\xed\xfa\xce", b"\xce\xfa\xed\xfe", b"\xfe\xed\xfa\xcf", b"\xcf\xfa\xed\xfe",
                        b"\xca\xfe\xba\xbe", b"\xbe\xba\xfe\xca"):
            if src.entry.ext == "class" and head[:4] == b"\xca\xfe\xba\xbe":
                return read_class(head)
            return read_macho(head)
        return None
