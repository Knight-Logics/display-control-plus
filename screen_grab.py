"""Capture one monitor rectangle, including negative virtual-desktop coordinates.

Pillow's Windows ImageGrab path asks for the whole virtual screen with
CreateDC("DISPLAY") and then BitBlt from SM_XVIRTUALSCREEN / SM_YVIRTUALSCREEN.
Those origins are negative when a display sits left of or above the primary, and
BitBlt then fails with OSError: screen grab failed.

GetDC(NULL) is the virtual screen, whose origin is the primary monitor, so the
same negative coordinates are valid there. This module grabs only the requested
rectangle.
"""

import ctypes
import sys


def normalize_bbox(bbox):
    if not isinstance(bbox, (list, tuple)) or len(bbox) != 4:
        raise ValueError(f"bbox must be left, top, right, bottom; got {bbox!r}")
    try:
        left, top, right, bottom = (int(value) for value in bbox)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"bbox coordinates must be integers; got {bbox!r}") from exc
    if right <= left or bottom <= top:
        raise ValueError(f"empty grab region: {bbox!r}")
    return left, top, right, bottom


def crop_box_for_virtual_desktop(bbox, virtual_origin):
    """Map a virtual-desktop rectangle onto an image whose origin is virtual_origin.

    Monitors left of or above the primary have negative desktop coordinates.
    Subtracting the virtual-screen origin produces a non-negative crop inside
    a full-desktop capture.
    """
    left, top, right, bottom = normalize_bbox(bbox)
    try:
        origin_x, origin_y = int(virtual_origin[0]), int(virtual_origin[1])
    except (TypeError, ValueError, IndexError) as exc:
        raise ValueError(f"virtual origin must be an x, y pair; got {virtual_origin!r}") from exc
    crop = (left - origin_x, top - origin_y, right - origin_x, bottom - origin_y)
    if crop[0] < 0 or crop[1] < 0:
        raise ValueError(f"bbox {bbox!r} is outside virtual desktop origin {virtual_origin!r}")
    return crop


def grab_region(bbox):
    left, top, right, bottom = normalize_bbox(bbox)
    if sys.platform != "win32":
        raise OSError(f"screen grab is only implemented on Windows (bbox {(left, top, right, bottom)})")
    return _grab_region_win32(left, top, right, bottom)


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [
        ("biSize", ctypes.c_uint32),
        ("biWidth", ctypes.c_int32),
        ("biHeight", ctypes.c_int32),
        ("biPlanes", ctypes.c_uint16),
        ("biBitCount", ctypes.c_uint16),
        ("biCompression", ctypes.c_uint32),
        ("biSizeImage", ctypes.c_uint32),
        ("biXPelsPerMeter", ctypes.c_int32),
        ("biYPelsPerMeter", ctypes.c_int32),
        ("biClrUsed", ctypes.c_uint32),
        ("biClrImportant", ctypes.c_uint32),
    ]


class BITMAPINFO(ctypes.Structure):
    _fields_ = [
        ("bmiHeader", BITMAPINFOHEADER),
        ("bmiColors", ctypes.c_uint32 * 3),
    ]


def _win_error(prefix):
    err = ctypes.get_last_error()
    if err:
        return f"{prefix} (WinError {err})"
    return prefix


def _grab_region_win32(left, top, right, bottom):
    width = right - left
    height = bottom - top
    gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
    user32 = ctypes.WinDLL("user32", use_last_error=True)

    user32.GetDC.argtypes = [ctypes.c_void_p]
    user32.GetDC.restype = ctypes.c_void_p
    user32.ReleaseDC.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    user32.ReleaseDC.restype = ctypes.c_int
    gdi32.CreateCompatibleDC.argtypes = [ctypes.c_void_p]
    gdi32.CreateCompatibleDC.restype = ctypes.c_void_p
    gdi32.CreateCompatibleBitmap.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int]
    gdi32.CreateCompatibleBitmap.restype = ctypes.c_void_p
    gdi32.SelectObject.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    gdi32.SelectObject.restype = ctypes.c_void_p
    gdi32.BitBlt.argtypes = [
        ctypes.c_void_p,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_void_p,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_uint32,
    ]
    gdi32.BitBlt.restype = ctypes.c_int
    gdi32.GetDIBits.argtypes = [
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_uint,
        ctypes.c_uint,
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_uint,
    ]
    gdi32.GetDIBits.restype = ctypes.c_int
    gdi32.DeleteObject.argtypes = [ctypes.c_void_p]
    gdi32.DeleteObject.restype = ctypes.c_int
    gdi32.DeleteDC.argtypes = [ctypes.c_void_p]
    gdi32.DeleteDC.restype = ctypes.c_int

    src_copy = 0x00CC0020
    dib_rgb_colors = 0
    screen = user32.GetDC(None)
    if not screen:
        raise OSError(_win_error("GetDC failed"))

    mem = bitmap = previous = None
    try:
        mem = gdi32.CreateCompatibleDC(screen)
        if not mem:
            raise OSError(_win_error("CreateCompatibleDC failed"))
        bitmap = gdi32.CreateCompatibleBitmap(screen, width, height)
        if not bitmap:
            raise OSError(_win_error(f"screen grab failed for bbox {(left, top, right, bottom)}"))
        previous = gdi32.SelectObject(mem, bitmap)
        if not previous:
            raise OSError(_win_error("SelectObject failed"))
        if not gdi32.BitBlt(mem, 0, 0, width, height, screen, int(left), int(top), src_copy):
            raise OSError(_win_error(f"screen grab failed for bbox {(left, top, right, bottom)}"))

        stride = (width * 3 + 3) & ~3
        buffer = ctypes.create_string_buffer(stride * height)
        info = BITMAPINFO()
        info.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
        info.bmiHeader.biWidth = width
        info.bmiHeader.biHeight = height
        info.bmiHeader.biPlanes = 1
        info.bmiHeader.biBitCount = 24
        info.bmiHeader.biCompression = 0
        info.bmiHeader.biSizeImage = stride * height
        rows = gdi32.GetDIBits(mem, bitmap, 0, height, buffer, ctypes.byref(info), dib_rgb_colors)
        if not rows:
            raise OSError(_win_error(f"screen grab failed for bbox {(left, top, right, bottom)}"))

        from PIL import Image

        return Image.frombytes("RGB", (width, height), buffer.raw, "raw", "BGR", stride, -1)
    finally:
        if previous and mem:
            gdi32.SelectObject(mem, previous)
        if bitmap:
            gdi32.DeleteObject(bitmap)
        if mem:
            gdi32.DeleteDC(mem)
        user32.ReleaseDC(None, screen)
