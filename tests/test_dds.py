"""DDS encoder: decode with Pillow and compare."""
import io, os, sys, unittest
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fakes
fakes.install()
import numpy as np
from edm_addon import edm_dds as D
try:
    from PIL import Image
except Exception:                      # pragma: no cover
    Image = None


def psnr(a, b):
    mse = ((a.astype(np.float32) - b.astype(np.float32)) ** 2).mean()
    return 99 if mse == 0 else 10 * np.log10(255 ** 2 / mse)


def sample(h=64, w=96, seed=1):
    rng = np.random.default_rng(seed)
    yy, xx = np.mgrid[0:h, 0:w]
    img = np.zeros((h, w, 4), np.uint8)
    img[..., 0] = xx * 255 // w
    img[..., 1] = yy * 255 // h
    img[..., 2] = 128 + (rng.random((h, w)) * 20).astype(np.uint8)
    img[..., 3] = 255
    img[20:40, 30:60] = (200, 30, 30, 255)
    return img


@unittest.skipIf(Image is None, 'Pillow missing')
class TestDds(unittest.TestCase):
    def _dec(self, data):
        im = Image.open(io.BytesIO(data)); im.load()
        return np.asarray(im.convert('RGBA'))

    def test_formats_roundtrip(self):
        for fmt, alpha in (('DXT1', False), ('DXT5', True), ('BGRA8', True), ('AUTO', False), ('AUTO', True)):
            a = sample()
            if alpha:
                a[..., 3] = np.clip(np.arange(a.shape[1])[None, :] * 255 // a.shape[1], 0, 255)
            data, used = D.encode_dds(a, fmt, mipmaps=True)
            dec = self._dec(data)
            self.assertEqual(dec.shape, a.shape)
            self.assertGreater(psnr(dec[..., :3], a[..., :3]), 30, fmt)
            if alpha:
                self.assertGreater(psnr(dec[..., 3], a[..., 3]), 35, fmt)
        self.assertEqual(D.encode_dds(sample(), 'AUTO')[1], 'DXT1')
        b = sample(); b[0, 0, 3] = 10
        self.assertEqual(D.encode_dds(b, 'AUTO')[1], 'DXT5')

    def test_odd_sizes(self):
        rng = np.random.default_rng(3)
        for hh, ww in ((5, 7), (1, 1), (2, 3), (30, 17)):
            a = (rng.random((hh, ww, 4)) * 255).astype(np.uint8); a[..., 3] = 255
            data, _ = D.encode_dds(a, 'DXT1')
            self.assertEqual(self._dec(data).shape[:2], (hh, ww))

    def test_size_accounting(self):
        a = np.full((256, 128, 4), 200, np.uint8)
        data, _ = D.encode_dds(a, 'DXT1', True)
        n, h, w = 0, 256, 128
        while True:
            n += D.level_size(w, h, 'DXT1')
            if h == 1 and w == 1:
                break
            h, w = max(h // 2, 1), max(w // 2, 1)
        self.assertEqual(len(data), 128 + n)

    def test_flat_colour_is_exact_enough(self):
        a = np.full((16, 16, 4), 255, np.uint8); a[..., :3] = (120, 80, 200)
        dec = self._dec(D.encode_dds(a, 'DXT1')[0])
        self.assertLess(np.abs(dec[..., :3].astype(int) - a[..., :3].astype(int)).max(), 8)


if __name__ == '__main__':
    unittest.main()
