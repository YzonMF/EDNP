"""Shrinking uploaded photos and scans before they are stored, so each document takes as little
storage as it can.

A phone photo or scan of a one-page document is usually 2-8 MB. Resized so its longer side is
at most MAX_SIDE pixels (about 170 dpi for an A4 page, plenty to read and print) and saved as a
JPEG at JPEG_QUALITY, it is usually 200-500 KB. It is also turned the right way up, and it loses
its metadata (EXIF), which in a phone photo can include where it was taken.

PDFs are left as they are: shrinking them safely needs tools the server doesn't have, so they
have a lower size limit instead (forms.MAX_STORED_BYTES).
"""

import io
import os

from django.core.files.uploadedfile import SimpleUploadedFile
from PIL import Image, ImageOps

MAX_SIDE = 2000
AVATAR_SIZE = 256
JPEG_QUALITY = 80
# Decoding takes about 4 bytes of memory per pixel. A PNG bigger than this is stored as it was
# uploaded rather than risk running the server out of memory. (A JPEG is decoded at a half,
# quarter or eighth of its size when it is that much bigger, so it is always shrunk.)
MAX_DECODE_PIXELS = 20_000_000


def _flattened(image):
    """The image in a mode a JPEG can hold: anything transparent goes onto white."""
    if image.mode in ('RGB', 'L'):
        return image
    if image.mode in ('RGBA', 'LA') or (image.mode == 'P' and 'transparency' in image.info):
        rgba = image.convert('RGBA')
        flat = Image.new('RGB', rgba.size, 'white')
        flat.paste(rgba, mask=rgba.getchannel('A'))
        return flat
    return image.convert('RGB')


def shrink(upload):
    """A smaller JPEG copy of `upload` (a JPG or PNG that passed the form's checks), or `upload`
    itself when the copy wouldn't be smaller or the image can't be read."""
    try:
        upload.seek(0)
        with Image.open(upload) as image:
            if image.format != 'JPEG' and image.width * image.height > MAX_DECODE_PIXELS:
                return upload
            image.thumbnail((MAX_SIDE, MAX_SIDE), Image.Resampling.LANCZOS)
            image = _flattened(ImageOps.exif_transpose(image))
            out = io.BytesIO()
            image.save(out, 'JPEG', quality=JPEG_QUALITY, optimize=True, progressive=True)
    except (OSError, ValueError, Image.DecompressionBombError):
        return upload
    finally:
        upload.seek(0)
    if out.tell() >= upload.size:
        return upload
    stem = os.path.splitext(os.path.basename(upload.name))[0]
    return SimpleUploadedFile(f'{stem}.jpg', out.getvalue(), content_type='image/jpeg')


def shrink_avatar(upload):
    """Center-crops and resizes `upload` to a standard AVATAR_SIZE x AVATAR_SIZE
    square JPEG, upright and stripped of EXIF metadata."""
    try:
        upload.seek(0)
        with Image.open(upload) as image:
            if image.format != 'JPEG' and image.width * image.height > MAX_DECODE_PIXELS:
                return upload
            image = ImageOps.exif_transpose(image)
            image = ImageOps.fit(image, (AVATAR_SIZE, AVATAR_SIZE), Image.Resampling.LANCZOS)
            image = _flattened(image)
            out = io.BytesIO()
            image.save(out, 'JPEG', quality=JPEG_QUALITY, optimize=True, progressive=True)
    except (OSError, ValueError, Image.DecompressionBombError):
        return upload
    finally:
        upload.seek(0)
    stem = os.path.splitext(os.path.basename(upload.name))[0]
    return SimpleUploadedFile(f'{stem}.jpg', out.getvalue(), content_type='image/jpeg')
