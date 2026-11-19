"""
Small DDS writer (DXT1 / DXT5 / BGRA8, with mipmaps). numpy only, no external tools,
so the add-on can turn the PNG/TGA a modder paints into the <name>.dds DCS loads.

Quality note: block compression uses the classic "bounding box" endpoint choice
(inset a bit). It is good for game textures; for final release art re-export with
a dedicated tool (Photoshop / NVIDIA Texture Tools / Compressonator) if you need
the last bit of quality.

encode_dds(rgba_uint8[h, w, 4], fmt='AUTO', mipmaps=True) -> (bytes, used_format)
  fmt: 'AUTO' (DXT1 when fully opaque, else DXT5), 'DXT1', 'DXT5', 'BGRA8'.
Row 0 of the array is the TOP row of the image.
"""
import struct

import numpy as np

DDSD_CAPS, DDSD_HEIGHT, DDSD_WIDTH, DDSD_PITCH = 0x1, 0x2, 0x4, 0x8
DDSD_PIXELFORMAT, DDSD_MIPMAPCOUNT, DDSD_LINEARSIZE = 0x1000, 0x20000, 0x80000
DDPF_ALPHAPIXELS, DDPF_FOURCC, DDPF_RGB = 0x1, 0x4, 0x40
DDSCAPS_COMPLEX, DDSCAPS_TEXTURE, DDSCAPS_MIPMAP = 0x8, 0x1000, 0x400000


def level_size(w, h, fmt):
    if fmt == 'BGRA8':
        return w * h * 4
    block = 8 if fmt == 'DXT1' else 16
    return max(1, (w + 3) // 4) * max(1, (h + 3) // 4) * block


def _next_level(a):
    """Box filter 2x2 (edge pixels replicated for odd sizes)."""
    h, w = a.shape[:2]
    if h % 2:
        a = np.concatenate([a, a[-1:]], axis=0)
    if w % 2:
        a = np.concatenate([a, a[:, -1:]], axis=1)
    f = a.astype(np.float32)
    f = (f[0::2, 0::2] + f[1::2, 0::2] + f[0::2, 1::2] + f[1::2, 1::2]) / 4.0
    nh, nw = max(h // 2, 1), max(w // 2, 1)
    return np.clip(f[:nh, :nw] + 0.5, 0, 255).astype(np.uint8)


def _blocks(a):
    """(h, w, c) -> (n_blocks, 16, c), blocks in row-major order, pixels row-major."""
    h, w, c = a.shape
    ph, pw = (-h) % 4, (-w) % 4
    if ph or pw:
        a = np.pad(a, ((0, ph), (0, pw), (0, 0)), mode='edge')
    H, W = a.shape[:2]
    b = a.reshape(H // 4, 4, W // 4, 4, c).transpose(0, 2, 1, 3, 4)
    return b.reshape(-1, 16, c)


def _to565(rgb):
    r = np.rint(rgb[..., 0] * 31.0 / 255.0).astype(np.uint16)
    g = np.rint(rgb[..., 1] * 63.0 / 255.0).astype(np.uint16)
    b = np.rint(rgb[..., 2] * 31.0 / 255.0).astype(np.uint16)
    return (r << 11) | (g << 5) | b


def _from565(c):
    r = ((c >> 11) & 31).astype(np.float32) * 255.0 / 31.0
    g = ((c >> 5) & 63).astype(np.float32) * 255.0 / 63.0
    b = (c & 31).astype(np.float32) * 255.0 / 31.0
    return np.stack([r, g, b], axis=-1)


def _encode_bc1_color(blocks_rgb):
    """blocks_rgb (n,16,3) float32 -> (n,) c0, c1 (uint16) and (n,) indices (uint32)."""
    n = blocks_rgb.shape[0]
    lo = blocks_rgb.min(axis=1)
    hi = blocks_rgb.max(axis=1)
    inset = (hi - lo) / 16.0
    lo = lo + inset
    hi = hi - inset
    c0 = _to565(hi)
    c1 = _to565(lo)
    swap = c0 < c1                          # keep c0 > c1 -> 4-colour mode
    c0, c1 = np.where(swap, c1, c0), np.where(swap, c0, c1)
    p0, p1 = _from565(c0), _from565(c1)
    pal = np.stack([p0, p1, (2 * p0 + p1) / 3.0, (p0 + 2 * p1) / 3.0], axis=1)   # (n,4,3)
    d = ((blocks_rgb[:, :, None, :] - pal[:, None, :, :]) ** 2).sum(axis=-1)      # (n,16,4)
    idx = d.argmin(axis=2).astype(np.uint32)                                       # (n,16)
    equal = (c0 == c1)
    idx[equal] = 0                                                                 # 3-colour mode quirk
    shifts = (np.arange(16, dtype=np.uint32) * 2)[None, :]
    packed = (idx << shifts).sum(axis=1, dtype=np.uint64).astype(np.uint32)
    return c0, c1, packed


def _encode_bc1(a):
    b = _blocks(a[..., :3]).astype(np.float32)
    c0, c1, idx = _encode_bc1_color(b)
    out = np.zeros((b.shape[0], 8), dtype=np.uint8)
    out[:, 0:2] = c0.astype('<u2').view(np.uint8).reshape(-1, 2)
    out[:, 2:4] = c1.astype('<u2').view(np.uint8).reshape(-1, 2)
    out[:, 4:8] = idx.astype('<u4').view(np.uint8).reshape(-1, 4)
    return out.tobytes()


def _encode_bc5_alpha(alpha):
    """alpha (n,16) uint8 -> (n,8) uint8 (DXT5 alpha block, 8-value mode)."""
    al = alpha.astype(np.float32)
    a0 = alpha.max(axis=1)
    a1 = alpha.min(axis=1)
    f0, f1 = a0.astype(np.float32), a1.astype(np.float32)
    pal = np.empty((alpha.shape[0], 8), dtype=np.float32)
    pal[:, 0], pal[:, 1] = f0, f1
    for k in range(2, 8):
        pal[:, k] = ((8 - k) * f0 + (k - 1) * f1) / 7.0
    d = np.abs(al[:, :, None] - pal[:, None, :])
    idx = d.argmin(axis=2).astype(np.uint64)                       # (n,16)
    idx[a0 == a1] = 0
    shifts = (np.arange(16, dtype=np.uint64) * 3)[None, :]
    bits = (idx << shifts).sum(axis=1, dtype=np.uint64)            # 48 bits
    out = np.zeros((alpha.shape[0], 8), dtype=np.uint8)
    out[:, 0], out[:, 1] = a0, a1
    raw = bits.astype('<u8').view(np.uint8).reshape(-1, 8)
    out[:, 2:8] = raw[:, :6]
    return out


def _encode_bc3(a):
    b = _blocks(a).astype(np.float32)
    c0, c1, idx = _encode_bc1_color(b[..., :3])
    n = b.shape[0]
    out = np.zeros((n, 16), dtype=np.uint8)
    out[:, 0:8] = _encode_bc5_alpha(_blocks(a[..., 3:4])[..., 0])
    out[:, 8:10] = c0.astype('<u2').view(np.uint8).reshape(-1, 2)
    out[:, 10:12] = c1.astype('<u2').view(np.uint8).reshape(-1, 2)
    out[:, 12:16] = idx.astype('<u4').view(np.uint8).reshape(-1, 4)
    return out.tobytes()


def _encode_level(a, fmt):
    if fmt == 'DXT1':
        return _encode_bc1(a)
    if fmt == 'DXT5':
        return _encode_bc3(a)
    bgra = a[..., [2, 1, 0, 3]]
    return np.ascontiguousarray(bgra).tobytes()


def _header(w, h, fmt, mips, linear_size):
    flags = DDSD_CAPS | DDSD_HEIGHT | DDSD_WIDTH | DDSD_PIXELFORMAT
    flags |= DDSD_PITCH if fmt == 'BGRA8' else DDSD_LINEARSIZE
    if mips > 1:
        flags |= DDSD_MIPMAPCOUNT
    if fmt == 'BGRA8':
        pf = struct.pack('<IIIIIIII', 32, DDPF_RGB | DDPF_ALPHAPIXELS, 0, 32,
                         0x00FF0000, 0x0000FF00, 0x000000FF, 0xFF000000)
        pitch = w * 4
    else:
        pf = struct.pack('<II4sIIIII', 32, DDPF_FOURCC, fmt.encode('ascii'), 0, 0, 0, 0, 0)
        pitch = linear_size
    caps = DDSCAPS_TEXTURE | ((DDSCAPS_COMPLEX | DDSCAPS_MIPMAP) if mips > 1 else 0)
    head = struct.pack('<4sIIIIIII', b'DDS ', 124, flags, h, w, pitch, 0, mips if mips > 1 else 0)
    head += b'\x00' * 44                       # reserved1[11]
    head += pf
    head += struct.pack('<IIIII', caps, 0, 0, 0, 0)
    assert len(head) == 128, len(head)
    return head


def encode_dds(rgba, fmt='AUTO', mipmaps=True):
    a = np.asarray(rgba)
    if a.ndim != 3 or a.shape[2] != 4 or a.dtype != np.uint8:
        raise ValueError('expected a uint8 array of shape (h, w, 4)')
    h, w = a.shape[:2]
    fmt = fmt.upper()
    if fmt == 'AUTO':
        fmt = 'DXT1' if bool((a[..., 3] == 255).all()) else 'DXT5'
    if fmt not in ('DXT1', 'DXT5', 'BGRA8'):
        raise ValueError('unknown format %r' % fmt)
    levels = [a]
    if mipmaps:
        cur = a
        while cur.shape[0] > 1 or cur.shape[1] > 1:
            cur = _next_level(cur)
            levels.append(cur)
    body = b''.join(_encode_level(l, fmt) for l in levels)
    head = _header(w, h, fmt, len(levels), level_size(w, h, fmt))
    return head + body, fmt
