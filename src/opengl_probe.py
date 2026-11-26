"""Probe a hidden Windows WGL context in an isolated subprocess, not a renderer."""
import ctypes as c
from ctypes import wintypes as w
import json
import os


def inspect_opengl():
    if os.name != 'nt':
        return {'available': False, 'reason': 'Windows WGL probe is not available on this platform.'}
    user, gdi, gl = c.WinDLL('user32'), c.WinDLL('gdi32'), c.WinDLL('opengl32')

    class PixelFormat(c.Structure):
        _fields_ = [('size', w.WORD), ('version', w.WORD), ('flags', w.DWORD),
                    ('pixel_type', w.BYTE), ('color_bits', w.BYTE),
                    ('red_bits', w.BYTE), ('red_shift', w.BYTE),
                    ('green_bits', w.BYTE), ('green_shift', w.BYTE),
                    ('blue_bits', w.BYTE), ('blue_shift', w.BYTE),
                    ('alpha_bits', w.BYTE), ('alpha_shift', w.BYTE),
                    ('accum_bits', w.BYTE), ('accum_red', w.BYTE),
                    ('accum_green', w.BYTE), ('accum_blue', w.BYTE),
                    ('accum_alpha', w.BYTE), ('depth_bits', w.BYTE),
                    ('stencil_bits', w.BYTE), ('aux_buffers', w.BYTE),
                    ('layer_type', w.BYTE), ('reserved', w.BYTE),
                    ('layer_mask', w.DWORD), ('visible_mask', w.DWORD),
                    ('damage_mask', w.DWORD)]

    user.CreateWindowExW.argtypes = [w.DWORD, w.LPCWSTR, w.LPCWSTR, w.DWORD,
                                    c.c_int, c.c_int, c.c_int, c.c_int,
                                    w.HWND, w.HMENU, w.HINSTANCE, c.c_void_p]
    user.CreateWindowExW.restype = w.HWND
    user.GetDC.argtypes, user.GetDC.restype = [w.HWND], w.HDC
    user.ReleaseDC.argtypes = [w.HWND, w.HDC]
    user.DestroyWindow.argtypes = [w.HWND]
    gdi.ChoosePixelFormat.argtypes = [w.HDC, c.POINTER(PixelFormat)]
    gdi.SetPixelFormat.argtypes = [w.HDC, c.c_int, c.POINTER(PixelFormat)]
    gl.wglCreateContext.argtypes, gl.wglCreateContext.restype = [w.HDC], w.HANDLE
    gl.wglMakeCurrent.argtypes = [w.HDC, w.HANDLE]
    gl.wglDeleteContext.argtypes = [w.HANDLE]
    gl.glGetString.argtypes, gl.glGetString.restype = [c.c_uint], c.c_char_p
    window = dc = context = None
    try:
        # Built-in STATIC class, no WS_VISIBLE: no application UI is displayed.
        window = user.CreateWindowExW(0, 'STATIC', 'OpenGL capability probe', 0,
                                      0, 0, 32, 32, None, None, None, None)
        if not window:
            raise OSError('Cannot create hidden WGL window.')
        dc = user.GetDC(window)
        fmt = PixelFormat()
        fmt.size, fmt.version = c.sizeof(fmt), 1
        fmt.flags, fmt.color_bits, fmt.depth_bits = 0x24, 24, 24
        pixel = gdi.ChoosePixelFormat(dc, c.byref(fmt))
        if not pixel or not gdi.SetPixelFormat(dc, pixel, c.byref(fmt)):
            raise OSError('No usable WGL pixel format.')
        context = gl.wglCreateContext(dc)
        if not context or not gl.wglMakeCurrent(dc, context):
            raise OSError('Cannot make WGL context current.')
        strings = {key: (gl.glGetString(value) or b'unknown').decode('utf-8', errors='replace')
                   for key, value in [('version', 0x1F02), ('vendor', 0x1F00), ('renderer', 0x1F01)]}
        return dict(available=True, **strings)
    finally:
        if context:
            gl.wglMakeCurrent(None, None)
            gl.wglDeleteContext(context)
        if dc:
            user.ReleaseDC(window, dc)
        if window:
            user.DestroyWindow(window)


if __name__ == '__main__':
    try:
        result = inspect_opengl()
    except Exception as error:
        result = {'available': False, 'reason': str(error)}
    print(json.dumps(result))
